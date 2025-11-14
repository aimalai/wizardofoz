import os
from datetime import datetime
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room

app = Flask(__name__, static_folder='static', template_folder='templates')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

LOG_PATH = "wizard_log.csv"

def log_row(row):
    header = "timestamp,action_type,action_id,description,expected_effect,participant_response,observer_note\n"
    if not os.path.exists(LOG_PATH):
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write(header)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(row + "\n")

@app.route('/')
def dashboard():
    return render_template("dashboard.html")

@app.route('/participant')
def participant():
    return render_template("participant.html")

@socketio.on('connect')
def on_connect():
    print("Client connected:", request.sid)
    emit('status', {'msg': 'connected', 'sid': request.sid})
    # notify wizard that a participant connected
    socketio.emit('participant_status', {'connected': True}, room='wizard')

@socketio.on('disconnect')
def on_disconnect():
    print("Client disconnected:", request.sid)
    # notify wizard that a participant disconnected
    socketio.emit('participant_status', {'connected': False}, room='wizard')

@socketio.on('join_wizard')
def on_join_wizard():
    """Optional: dashboard can call this to join the wizard room so it receives participant_input events."""
    try:
        join_room('wizard')
        emit('status', {'msg': 'joined_wizard', 'sid': request.sid})
        print(f"Socket {request.sid} joined wizard room")
    except Exception as e:
        print("join_wizard error", e)

@socketio.on('trigger_action')
def handle_trigger(data):
    """
    Expected data structure:
    { type: 'audio'|'haptic'|'text'|'beacon'|'inject_correction',
      id: 'overview_01', payload: { ... }, note: 'optional' }
    """
    timestamp = datetime.utcnow().isoformat() + "Z"
    desc = str(data.get("payload", ""))
    note = data.get("note", "")
    # keep CSV shape compatible with existing header
    row = f'{timestamp},{data.get("type")},{data.get("id")},"{desc}","{note}",,""'
    log_row(row.strip())
    emit('action', data, broadcast=True)
    emit('ack', {'status': 'sent', 'id': data.get('id')})

@socketio.on('participant_ack')
def on_participant_ack(data):
    timestamp = datetime.utcnow().isoformat() + "Z"
    row = f'{timestamp},ack,{data.get("id")},"participant ack","",{data.get("response","")},"{data.get("note","")}"'
    log_row(row.strip())
    print("Participant ack:", data)

@socketio.on('participant_input')
def on_participant_input(payload):
    """
    Expected payload from participant UI:
    { runId, id, type: 'choice'|'text', value, clientTs }
    This forwards the input to wizard room and appends to the same log file.
    """
    server_ts = datetime.utcnow().isoformat() + "Z"
    # normalize fields
    run_id = payload.get('runId') or ''
    pid = payload.get('id') or ''
    ptype = payload.get('type') or 'participant_input'
    value = payload.get('value') or ''
    client_ts = payload.get('clientTs') or ''

    # Row: timestamp,action_type,action_id,description,expected_effect,participant_response,observer_note
    desc = f'run:{run_id};clientTs:{client_ts}'
    row = f'{server_ts},participant_input,{pid},"{desc}","",{value},"runId:{run_id};clientTs:{client_ts}"'
    log_row(row.strip())

    # Forward to wizard operator(s)
    try:
        socketio.emit('participant_input', {
            'runId': run_id,
            'id': pid,
            'type': ptype,
            'value': value,
            'clientTs': client_ts,
            'serverTs': server_ts
        }, room='wizard')
    except Exception as e:
        print("Emit participant_input failed:", e)

    # Optionally acknowledge back to participant
    emit('participant_input_ack', {'status': 'received', 'id': pid, 'serverTs': server_ts})
    print("Participant input:", payload)

@socketio.on('participant_confirm')
def on_participant_confirm(data):
    """
    Expected payload from participant confirmation:
    { id: 'confirm_hearing', response: 'confirmed' }
    """
    timestamp = datetime.utcnow().isoformat() + "Z"
    pid = data.get('id') or 'confirm'
    response = data.get('response') or ''
    note = data.get('note', '')

    # Log row
    row = f'{timestamp},participant_confirm,{pid},"participant confirmed hearing","",{response},"{note}"'
    log_row(row.strip())

    # Forward to wizard operator(s)
    try:
        socketio.emit('participant_input', {
            'id': pid,
            'type': 'confirm',
            'value': response,
            'serverTs': timestamp
        }, room='wizard')
    except Exception as e:
        print("Emit participant_confirm failed:", e)

    print("Participant confirm:", data)

if __name__ == '__main__':
    from gevent import pywsgi
    from geventwebsocket.handler import WebSocketHandler
    server = pywsgi.WSGIServer(('0.0.0.0', 5000), app, handler_class=WebSocketHandler)
    server.serve_forever()
