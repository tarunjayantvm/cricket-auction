import os
from flask import Flask, render_template, redirect, url_for, request, session, flash, jsonify, send_from_directory
from flask_socketio import SocketIO, emit
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
import threading
import random
import pyttsx3

app = Flask(__name__)
app.secret_key = 'your_secret_key'
socketio = SocketIO(app)
login_manager = LoginManager()
login_manager.init_app(app)

# Configuration for file uploads
UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

INITIAL_CAPITAL = 1000
DEFAULT_BASE_PRICE = 25

users = {}
players = []
bidders = {}
sold_players = []
unsold_players = []
current_player = None
timer_end = None
highest_bid = 0
highest_bidder = None

class User(UserMixin):
    def __init__(self, username, user_type):
        self.username = username
        self.user_type = user_type
        self.id = username

    def get_id(self):
        return self.id

@login_manager.user_loader
def load_user(username):
    return users.get(username)

def speak(text):
    def run():
        engine = pyttsx3.init()
        engine.setProperty('rate', 150)
        engine.setProperty('volume', 1.0)
        engine.say(text)
        engine.runAndWait()
    threading.Thread(target=run, daemon=True).start()

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

    if username == 'admin' and password == 'admin123':
        user = User(username, 'admin')
        users[username] = user
        login_user(user)
        return jsonify({'success': True, 'is_admin': True}), 200
    else:
        user = User(username, 'bidder')
        users[username] = user
        login_user(user)
        if username not in bidders:
            bidders[username] = {'capital': INITIAL_CAPITAL, 'current_bid': 0, 'bids': []}
        return jsonify({'success': True, 'is_admin': False}), 200

@app.route('/admin')
@login_required
def admin():
    return render_template('admin.html', players=players, bidders=bidders)

@app.route('/add_player', methods=['POST'])
@login_required
def add_player():
    name = request.form.get('name')
    type_ = request.form.get('type')
    runs = request.form.get('runs')
    average = request.form.get('average')
    strike_rate = request.form.get('strike_rate')
    image_file = request.files.get('image')

    if not name or not type_:
        flash("Name and Type are required", "danger")
        return redirect(url_for('admin'))

    filename = None
    if image_file and allowed_file(image_file.filename):
        filename = secure_filename(f"{name}_{image_file.filename}")
        image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        image_file.save(image_path)

    players.append({
        'name': name,
        'type': type_,
        'runs': runs,
        'average': average,
        'strike_rate': strike_rate,
        'image': filename
    })

    flash(f"Player {name} added successfully", "success")
    return redirect(url_for('admin'))

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/bidder')
@login_required
def bidder():
    capital = bidders.get(current_user.username, {'capital': INITIAL_CAPITAL})['capital']
    return render_template('bidder.html', bidders=bidders, current_player=current_player, highest_bid=highest_bid, capital=capital)

@app.route('/start_auction', methods=['POST'])
@login_required
def start_auction():
    global current_player, timer_end, highest_bid, highest_bidder
    if players:
        index = random.randrange(len(players))
        current_player = players.pop(index)
        highest_bid = DEFAULT_BASE_PRICE
        highest_bidder = None
        timer_end = datetime.now() + timedelta(seconds=20)
        speak(f"Auction started for {current_player['name']} with base price {highest_bid} lakhs.")
        socketio.emit('auction_started', current_player, broadcast=True)
    return redirect(url_for('admin'))

@app.route('/bid', methods=['POST'])
@login_required
def bid():
    global highest_bid, highest_bidder, timer_end
    bid_amount = int(request.form['bid_amount'])
    username = current_user.username
    bidder_info = bidders[username]
    available = bidder_info['capital'] + bidder_info.get('current_bid', 0)
    
    if bid_amount > highest_bid and bid_amount <= available:
        if highest_bidder:
            bidders[highest_bidder]['capital'] += bidders[highest_bidder]['current_bid']
            bidders[highest_bidder]['current_bid'] = 0
        
        bidder_info['capital'] -= bid_amount
        bidder_info['current_bid'] = bid_amount
        highest_bid = bid_amount
        highest_bidder = username
        timer_end = datetime.now() + timedelta(seconds=20)
        speak(f"New bid! {username} bids {highest_bid} lakhs.")
        socketio.emit('new_bid', {'bidder': username, 'amount': highest_bid}, broadcast=True)
    return redirect(url_for('bidder'))

@socketio.on('mark_sold')
def handle_mark_sold():
    global current_player, highest_bid, highest_bidder, sold_players
    if current_player:
        current_player['status'] = 'sold'
        current_player['winner'] = highest_bidder
        current_player['sold_price'] = highest_bid
        sold_players.append(current_player)
        speak(f"Sold! {current_player['name']} goes to {highest_bidder} for {highest_bid} lakhs.")
        socketio.emit('auction_end', {'player': current_player['name'], 'winner': highest_bidder, 'amount': highest_bid}, broadcast=True)
        current_player = None

@socketio.on('mark_unsold')
def handle_mark_unsold():
    global current_player, unsold_players
    if current_player:
        current_player['status'] = 'unsold'
        unsold_players.append(current_player)
        speak(f"{current_player['name']} remains unsold.")
        socketio.emit('auction_end', {'player': current_player['name'], 'winner': None}, broadcast=True)
        current_player = None

@socketio.on('check_timer')
def check_timer():
    global current_player, timer_end, highest_bidder, highest_bid, sold_players, unsold_players
    if timer_end and datetime.now() >= timer_end:
        if highest_bidder:
            current_player['status'] = 'sold'
            current_player['winner'] = highest_bidder
            current_player['sold_price'] = highest_bid
            sold_players.append(current_player)
        else:
            current_player['status'] = 'unsold'
            unsold_players.append(current_player)
        socketio.emit('auction_end', {'player': current_player['name'], 'winner': highest_bidder, 'amount': highest_bid}, broadcast=True)
        current_player = None
        timer_end = None
        highest_bid = 0
        highest_bidder = None
        for bidder in bidders.values():
            bidder['current_bid'] = 0
        socketio.emit('admin_update', {'sold_players': sold_players, 'unsold_players': unsold_players, 'bidders': bidders}, broadcast=True)

if __name__ == '__main__':
    socketio.run(app, debug=True)
