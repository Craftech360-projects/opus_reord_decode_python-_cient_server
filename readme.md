# Real-time Audio Streaming System

This project provides a real-time audio streaming solution using WebSockets, Opus codec, and Python. It consists of a server that receives and processes audio streams, and a client that captures and sends audio from a microphone.

## Features
- Real-time audio streaming using WebSockets
- Opus codec for efficient audio compression
- Keyboard controls for starting/stopping audio streaming
- Automatic audio playback and saving on server
- Cross-platform support (Windows, macOS, Linux)

## Prerequisites
- Python 3.10+
- pip package manager
- Microphone

## Installation

### 1. Clone the repository
```bash
git clone https://github.com/Craftech360-projects/opus_reord_decode_python-_cient_server.git
cd audio-streaming-project
```

### 2. Create and activate virtual environment
```bash
python -m venv myenv
# Windows:
myenv\Scripts\activate
# Linux/macOS:
source myenv/bin/activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

## Project Structure
```
├── client/               # Client application
│   └── client.py         # Main client script
├── server/               # Server application
│   └── server.py         # Main server script
├── audio_protocol.md     # Audio streaming protocol specification
├── requirements.txt      # Python dependencies
└── README.md             # This documentation
```

## Usage

### Starting the Server
```bash
cd server
python server.py
```

The server will start on `http://localhost:5000` and listen for WebSocket connections at `/ws/audio`.

### Running the Client
```bash
cd client
python client.py
```

Client controls:
- Press SPACEBAR to start/stop audio streaming
- Press CTRL+C to exit

### Audio Streaming Protocol
The communication protocol between client and server is documented in [audio_protocol.md](audio_protocol.md). Key points:
1. Client sends 'start' message with session parameters
2. Server acknowledges with 'ack' message
3. Client sends audio frames as binary data
4. Client sends 'stop' message to end session
5. Server processes audio and saves as WAV file

## Troubleshooting

### Common Issues
1. **WebSocket connection errors**:
   - Ensure server is running before starting client
   - Verify server URI in client.py matches server address

2. **Microphone access issues**:
   - Check microphone permissions
   - Verify microphone is connected and working

3. **Dependency issues**:
   - Ensure all packages are installed from requirements.txt
   - Use Python 3.10 or newer

### Error Messages
- `websockets.exceptions.InvalidStatusCode: HTTP 403`: Incorrect server endpoint
- `Microphone access failed`: Microphone not available or in use
- `Opus encoding error`: Audio frame size mismatch

## Contributing
Contributions are welcome! Please follow these steps:
1. Fork the repository
2. Create a new feature branch
3. Commit your changes
4. Push to the branch
5. Submit a pull request

## License
This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.


docker build -t rahulpscraftech360/websocketcheeko:latest .

docker push rahulpscraftech360/websocketcheeko:latest