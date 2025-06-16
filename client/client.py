import asyncio
import websockets
import json
import uuid
import pyaudio
import opuslib
import struct
import keyboard  # For keyboard controls
import threading
import time
import queue

# Audio configuration (matches protocol spec)
SAMPLE_RATE = 16000  # Hz
CHANNELS = 1
FRAME_DURATION = 20  # ms
FRAME_SIZE = int(SAMPLE_RATE * FRAME_DURATION / 1000)

class AudioClient:
    def __init__(self, server_uri="ws://localhost:5000/ws/audio"):
        self.server_uri = server_uri
        self.session_id = None
        try:
            self.encoder = opuslib.Encoder(SAMPLE_RATE, CHANNELS, 'voip')
            self.decoder = opuslib.Decoder(SAMPLE_RATE, CHANNELS)
        except opuslib.OpusError as e:
            print(f"Opus encoder/decoder initialization failed: {e}")
            raise
        try:
            self.audio = pyaudio.PyAudio()
        except Exception as e:
            print(f"PyAudio initialization failed: {e}")
            raise
        self.input_stream = None
        self.output_stream = None
        self.streaming_active = False  # Controls audio streaming state
        self.space_pressed = False  # Flag for space key press
        self.audio_queue = queue.Queue()  # Queue for audio playback
        self.playback_thread = None
        self.playback_active = False
        self.opus_frame_size = FRAME_SIZE  # Will be updated based on server response
        
    async def connect(self):
        # Add Origin header for CORS compatibility
        try:
            self.websocket = await websockets.connect(
                self.server_uri,
                extra_headers={"Origin": "http://localhost"}
            )
        except websockets.exceptions.InvalidHandshake as e:
            # Handle potential SSL errors in development
            if "SSL" in str(e) or "TLS" in str(e):
                print(f"SSL error occurred: {e}. Attempting to bypass for development...")
                import ssl
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
                self.websocket = await websockets.connect(
                    self.server_uri,
                    extra_headers={"Origin": "http://localhost"},
                    ssl=ssl_context
                )
            else:
                raise
        
    def start_capture(self):
        """Start capturing audio from microphone"""
        try:
            self.input_stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                frames_per_buffer=FRAME_SIZE
            )
        except Exception as e:
            print(f"Microphone access failed: {e}")
            raise

    def start_playback(self):
        """Initialize audio output stream for playback"""
        try:
            self.output_stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                output=True,
                frames_per_buffer=FRAME_SIZE
            )
            
            # Start playback thread
            self.playback_active = True
            self.playback_thread = threading.Thread(target=self.playback_worker, daemon=True)
            self.playback_thread.start()
            print("Audio output stream and playback thread initialized")
        except Exception as e:
            print(f"Audio output initialization failed: {e}")
            raise

    def playback_worker(self):
        """Worker thread for audio playback"""
        while self.playback_active:
            try:
                # Get audio data from queue with timeout
                audio_data = self.audio_queue.get(timeout=0.1)
                if audio_data is None:  # Poison pill to stop thread
                    break
                
                # Play the audio data
                if self.output_stream and not self.output_stream.is_stopped():
                    self.output_stream.write(audio_data)
                    
            except queue.Empty:
                continue
            except Exception as e:
                print(f"Playback error: {e}")
                continue

    def queue_audio_for_playback(self, pcm_data):
        """Queue PCM audio data for playback"""
        try:
            self.audio_queue.put(pcm_data)
        except Exception as e:
            print(f"Error queuing audio: {e}")

    async def handle_server_messages(self):
        """Handle incoming messages from server (including audio responses)"""
        try:
            while True:
                try:
                    message = await self.websocket.recv()
                    
                    # Check if it's JSON or binary data
                    if isinstance(message, str):
                        # JSON message
                        try:
                            data = json.loads(message)
                            print(f"📨 Received JSON: {data.get('type', 'unknown')}")
                            
                            if data.get('type') == 'audio_response':
                                print(f"🎵 Audio response incoming:")
                                print(f"   Format: {data.get('format')}")
                                print(f"   Sample Rate: {data.get('sample_rate')}Hz")
                                print(f"   Channels: {data.get('channels')}")
                                print(f"   Frame Count: {data.get('frame_count')}")
                                
                                # Initialize playback if not already done
                                if not self.output_stream:
                                    self.start_playback()
                                    
                            elif data.get('type') == 'ack':
                                print(f"✅ Session acknowledged: {data.get('status')}")
                                
                            elif data.get('type') == 'error':
                                print(f"❌ Server error: {data.get('message')}")
                            elif data.get('type') == 'session_status' and data.get('status') == 'session_ended':
                                print("✅ Server signaled end of session.")
                                self.streaming_active = False
                                
                            elif data.get('type') == 'stop':
                                print("🛑 Received stop signal from server")
                                self.streaming_active = False

                                
                        except json.JSONDecodeError:
                            print(f"❌ Invalid JSON received: {message}")
                            
                    elif isinstance(message, bytes):
                        # Binary audio data - could be raw PCM or framed Opus
                        if len(message) >= 4:
                            # Check if it's framed Opus data (4-byte length header)
                            frame_length = struct.unpack('>I', message[:4])[0]
                            if len(message) == frame_length + 4:
                                # This is framed Opus data - decode it
                                opus_frame = message[4:]
                                try:
                                    pcm_data = self.decoder.decode(opus_frame, FRAME_SIZE)
                                    print(f"🔊 Decoded Opus frame: {len(opus_frame)} -> {len(pcm_data)} PCM bytes")
                                    self.queue_audio_for_playback(pcm_data)
                                except opuslib.OpusError as e:
                                    print(f"❌ Opus decode error: {e}")
                            else:
                                # Assume raw PCM data
                                print(f"🔊 Received PCM audio: {len(message)} bytes")
                                self.queue_audio_for_playback(message)
                        else:
                            # Small data, assume PCM
                            print(f"🔊 Received PCM audio: {len(message)} bytes")
                            self.queue_audio_for_playback(message)
                        
                except websockets.exceptions.ConnectionClosed:
                    print("🔌 Server connection closed")
                    break
                except Exception as e:
                    print(f"❌ Error handling server message: {e}")
                    continue
                    
        except Exception as e:
            print(f"❌ Fatal error in message handler: {e}")
            
    async def start_session(self):
        """Start a new audio session"""
        self.session_id = str(uuid.uuid4())
        await self.websocket.send(json.dumps({
            'type': 'start',
            'session_id': self.session_id,
            'sample_rate': SAMPLE_RATE,
            'channels': CHANNELS,
            'frame_duration': FRAME_DURATION
        }))
        print(f"📤 Started session: {self.session_id}")
        
    async def stop_session(self):
        """Stop the current audio session"""
        if self.session_id:
            await self.websocket.send(json.dumps({
                'type': 'stop',
                'session_id': self.session_id
            }))
            print(f"📤 Stopped session: {self.session_id}")
            self.session_id = None

    async def handle_space_press(self):
        """Handle space key press to toggle streaming"""
        self.streaming_active = not self.streaming_active
        if self.streaming_active:
            await self.start_session()
            print("\n🎤 Audio streaming STARTED")
        else:
            await self.stop_session()
            print("\n🛑 Audio streaming STOPPED")
        
    async def stream_audio(self):
        """Capture and stream audio to server when active"""
        try:
            while True:
                # Check for space key press
                if self.space_pressed:
                    self.space_pressed = False
                    await self.handle_space_press()
                
                if self.streaming_active:
                    try:
                        # Use non-blocking read with timeout
                        if self.input_stream.get_read_available() >= FRAME_SIZE:
                            # Read audio data
                            raw_frame = self.input_stream.read(FRAME_SIZE, exception_on_overflow=False)
                            
                            try:
                                # Encode to Opus
                                opus_frame = self.encoder.encode(raw_frame, FRAME_SIZE)
                            except opuslib.OpusError as e:
                                print(f"Opus encoding error: {e}")
                                continue
                            
                            # Create message: [4-byte length][Opus frame]
                            frame_length = len(opus_frame)
                            message = struct.pack('>I', frame_length) + opus_frame
                            
                            try:
                                # Send to server
                                await self.websocket.send(message)
                            except websockets.exceptions.ConnectionClosed:
                                print("WebSocket connection closed unexpectedly")
                                break
                            except Exception as e:
                                print(f"Error sending audio: {e}")
                                break
                        else:
                            # Short sleep if no audio data available
                            await asyncio.sleep(0.01)
                            
                    except Exception as e:
                        print(f"Streaming error: {e}")
                        break
                else:
                    # Avoid high CPU usage while inactive
                    await asyncio.sleep(0.1)
                    
        except Exception as e:
            print(f"Fatal streaming error: {e}")
            
    async def stop(self):
        """Stop streaming and close connection"""
        await self.stop_session()
        
        # Stop playback
        self.playback_active = False
        if self.playback_thread:
            self.audio_queue.put(None)  # Poison pill
            self.playback_thread.join(timeout=1.0)
        
        # Close streams
        if self.input_stream:
            self.input_stream.stop_stream()
            self.input_stream.close()
        if self.output_stream:
            self.output_stream.stop_stream()
            self.output_stream.close()
            
        self.audio.terminate()
        await self.websocket.close()

async def main():
    client = AudioClient()
    await client.connect()
    client.start_capture()
    
    # Start the message handler as a background task
    message_handler_task = asyncio.create_task(client.handle_server_messages())
    
    # Keyboard control setup
    print("🎤 Press SPACE to start/stop audio streaming. Press Ctrl+C to exit.")
    print("🔊 Audio responses will be played automatically when received.")
    
    # Keyboard event handler (runs in separate thread)
    def on_key_event(e):
        if e.event_type == keyboard.KEY_DOWN and e.name == 'space':
            client.space_pressed = True
    
    # Register the keyboard handler
    keyboard.hook(on_key_event)
    
    try:
        # Run both audio streaming and message handling concurrently
        await asyncio.gather(
            client.stream_audio(),
            message_handler_task
        )
    except KeyboardInterrupt:
        print("\n🛑 Stopping...")
        message_handler_task.cancel()
        await client.stop()
    except Exception as e:
        print(f"❌ Application error: {e}")
        message_handler_task.cancel()
        await client.stop()
    finally:
        keyboard.unhook_all()

if __name__ == "__main__":
    asyncio.run(main())