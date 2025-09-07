import pyttsx3

engine = pyttsx3.init()
engine.setProperty('rate', 150)
engine.setProperty('volume', 1.0)
engine.say("Test message. Auction speech is working.")
engine.runAndWait()
