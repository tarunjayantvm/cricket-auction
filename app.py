import os
from flask import Flask, render_template, redirect, url_for, request, session, flash, jsonify, send_from_directory
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
import threading
import random
import uuid

app = Flask(__name__)
app.secret_key = 'namma_cricket_secret_key_2025'
socketio = SocketIO(app, cors_allowed_origins="*")
login_manager = LoginManager()
login_manager.init_app(app)

# Configuration for file uploads
UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

INITIAL_CAPITAL = 1000
DEFAULT_BASE_PRICE = 25

# Global data structures
users = {}
auction_events = {}
current_event = None
players = []
bidders = {}
sold_players = []
unsold_players = []
current_player = None
highest_bid = 0
highest_bidder = None
auction_active = False
spectators = []

class User(UserMixin):
    def __init__(self, username, user_type, full_name=None):
        self.username = username
        self.user_type = user_type
        self.full_name = full_name or username
        self.id = username

    def get_id(self):
        return self.id

@login_manager.user_loader
def load_user(username):
    return users.get(username)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def index():
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': 'Invalid request format'}), 400

    username = data.get('username')
    password = data.get('password')
    full_name = data.get('full_name', username)

    if username == 'admin' and password == 'admin123':
        user = User(username, 'admin', 'Admin')
        users[username] = user
        login_user(user)
        return jsonify({'success': True, 'is_admin': True}), 200
    else:
        user = User(username, 'bidder', full_name)
        users[username] = user
        login_user(user)
        if username not in bidders:
            bidders[username] = {
                'capital': INITIAL_CAPITAL, 
                'current_bid': 0, 
                'bids': [],
                'full_name': full_name,
                'purchased_players': []
            }
        return jsonify({'success': True, 'is_admin': False}), 200

@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.user_type == 'admin':
        return redirect(url_for('admin'))
    else:
        return render_template('dashboard.html', 
                             user=current_user, 
                             auction_events=auction_events,
                             current_event=current_event)

@app.route('/admin')
@login_required
def admin():
    if current_user.user_type != 'admin':
        return redirect(url_for('dashboard'))
    return render_template('admin.html', 
                         players=players, 
                         bidders=bidders,
                         auction_events=auction_events,
                         current_event=current_event)

@app.route('/create_event', methods=['POST'])
@login_required
def create_event():
    if current_user.user_type != 'admin':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    
    data = request.get_json()
    event_id = str(uuid.uuid4())
    event_data = {
        'id': event_id,
        'name': data.get('name'),
        'description': data.get('description'),
        'max_players': int(data.get('max_players', 50)),
        'base_price': int(data.get('base_price', 25)),
        'created_at': datetime.now(),
        'status': 'created',
        'registered_players': []
    }
    
    auction_events[event_id] = event_data
    socketio.emit('event_created', event_data, broadcast=True)
    return jsonify({'success': True, 'event': event_data})

@app.route('/activate_event/<event_id>', methods=['POST'])
@login_required
def activate_event(event_id):
    global current_event
    if current_user.user_type != 'admin':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    
    if event_id in auction_events:
        current_event = auction_events[event_id]
        current_event['status'] = 'active'
        socketio.emit('event_activated', current_event, broadcast=True)
        return jsonify({'success': True})
    return jsonify({'success': False, 'message': 'Event not found'})

@app.route('/register_for_event', methods=['POST'])
@login_required
def register_for_event():
    if not current_event:
        return jsonify({'success': False, 'message': 'No active event'})
    
    name = request.form.get('name')
    role = request.form.get('role')
    team = request.form.get('team')
    stats = request.form.get('stats')
    image_file = request.files.get('image')
    
    filename = None
    if image_file and allowed_file(image_file.filename):
        filename = secure_filename(f"{current_user.username}_{image_file.filename}")
        image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        image_file.save(image_path)
    
    player_data = {
        'name': name,
        'role': role,
        'team': team,
        'stats': stats,
        'image': filename,
        'registered_by': current_user.username,
        'registered_at': datetime.now()
    }
    
    current_event['registered_players'].append(player_data)
    players.append(player_data)
    
    socketio.emit('player_registered', player_data, broadcast=True)
    return jsonify({'success': True})

@app.route('/add_player', methods=['POST'])
@login_required
def add_player():
    if current_user.user_type != 'admin':
        return redirect(url_for('dashboard'))
        
    name = request.form.get('name')
    role = request.form.get('role')
    team = request.form.get('team')
    stats = request.form.get('stats')
    image_file = request.files.get('image')

    if not name or not role:
        flash("Name and Role are required", "danger")
        return redirect(url_for('admin'))

    filename = None
    if image_file and allowed_file(image_file.filename):
        filename = secure_filename(f"{name}_{image_file.filename}")
        image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        image_file.save(image_path)

    players.append({
        'name': name,
        'role': role,
        'team': team,
        'stats': stats,
        'image': filename
    })

    flash(f"Player {name} added successfully", "success")
    return redirect(url_for('admin'))

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/auction')
@login_required
def auction():
    user_role = 'admin' if current_user.user_type == 'admin' else 'bidder'
    return render_template('auction.html', 
                         user=current_user,
                         user_role=user_role,
                         bidders=bidders, 
                         current_player=current_player, 
                         highest_bid=highest_bid,
                         sold_players=sold_players,
                         auction_active=auction_active)

@app.route('/spectate')
@login_required
def spectate():
    return render_template('spectate.html',
                         user=current_user,
                         current_player=current_player,
                         highest_bid=highest_bid,
                         sold_players=sold_players,
                         auction_active=auction_active)

@app.route('/start_auction', methods=['POST'])
@login_required
def start_auction():
    global current_player, highest_bid, highest_bidder, auction_active
    if current_user.user_type != 'admin':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
        
    if players:
        index = random.randrange(len(players))
        current_player = players.pop(index)
        highest_bid = current_event['base_price'] if current_event else DEFAULT_BASE_PRICE
        highest_bidder = None
        auction_active = True
        
        socketio.emit('auction_started', {
            'player': current_player,
            'base_price': highest_bid
        }, broadcast=True)
        
        return jsonify({'success': True})
    return jsonify({'success': False, 'message': 'No players available'})

@app.route('/bid', methods=['POST'])
@login_required
def bid():
    global highest_bid, highest_bidder
    if not auction_active:
        return jsonify({'success': False, 'message': 'No active auction'})
        
    bid_amount = int(request.form['bid_amount'])
    username = current_user.username
    bidder_info = bidders[username]
    available = bidder_info['capital'] + bidder_info.get('current_bid', 0)
    
    if bid_amount > highest_bid and bid_amount <= available:
        # Return previous bid to previous bidder
        if highest_bidder and highest_bidder in bidders:
            bidders[highest_bidder]['capital'] += bidders[highest_bidder]['current_bid']
            bidders[highest_bidder]['current_bid'] = 0
        
        # Set new bid
        bidder_info['capital'] -= bid_amount
        bidder_info['current_bid'] = bid_amount
        highest_bid = bid_amount
        highest_bidder = username
        
        socketio.emit('new_bid', {
            'bidder': username,
            'bidder_name': bidder_info['full_name'],
            'amount': highest_bid
        }, broadcast=True)
        
        return jsonify({'success': True})
    return jsonify({'success': False, 'message': 'Invalid bid'})

# Socket events
@socketio.on('join_auction')
def on_join_auction(data):
    join_room('auction')
    emit('user_joined', {'username': current_user.username}, room='auction')

@socketio.on('admin_voice')
def handle_admin_voice(data):
    if current_user.user_type == 'admin':
        emit('admin_voice_broadcast', {
            'audio_data': data['audio_data'],
            'timestamp': datetime.now().isoformat()
        }, broadcast=True, include_self=False)

@socketio.on('bidder_voice')
def handle_bidder_voice(data):
    if current_user.user_type == 'bidder':
        emit('bidder_voice_broadcast', {
            'audio_data': data['audio_data'],
            'bidder': current_user.username,
            'bidder_name': bidders.get(current_user.username, {}).get('full_name', current_user.username),
            'timestamp': datetime.now().isoformat()
        }, broadcast=True, include_self=False)

@socketio.on('mark_sold')
def handle_mark_sold():
    global current_player, highest_bid, highest_bidder, sold_players, auction_active
    if current_user.user_type != 'admin':
        return
        
    if current_player and auction_active:
        current_player['status'] = 'sold'
        current_player['winner'] = highest_bidder
        current_player['winner_name'] = bidders.get(highest_bidder, {}).get('full_name', highest_bidder) if highest_bidder else None
        current_player['sold_price'] = highest_bid
        sold_players.append(current_player)
        
        # Add to winner's purchased players
        if highest_bidder and highest_bidder in bidders:
            bidders[highest_bidder]['purchased_players'].append(current_player)
        
        socketio.emit('auction_end', {
            'player': current_player['name'],
            'winner': highest_bidder,
            'winner_name': current_player['winner_name'],
            'amount': highest_bid,
            'status': 'sold'
        }, broadcast=True)
        
        current_player = None
        auction_active = False
        reset_bids()

@socketio.on('mark_unsold')
def handle_mark_unsold():
    global current_player, unsold_players, auction_active
    if current_user.user_type != 'admin':
        return
        
    if current_player and auction_active:
        current_player['status'] = 'unsold'
        unsold_players.append(current_player)
        
        socketio.emit('auction_end', {
            'player': current_player['name'],
            'winner': None,
            'status': 'unsold'
        }, broadcast=True)
        
        current_player = None
        auction_active = False
        reset_bids()

def reset_bids():
    global highest_bid, highest_bidder
    # Return current bids to bidders
    for bidder in bidders.values():
        bidder['capital'] += bidder.get('current_bid', 0)
        bidder['current_bid'] = 0
    highest_bid = 0
    highest_bidder = None

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)