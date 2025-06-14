# WebSocket Audio Streaming Protocol Specification (v2.0)

## 1. Message Types

### START Message (JSON)
```json
{
  "type": "start",
  "session_id": "uuid4",
  "sample_rate": 16000,
  "channels": 1,
  "frame_duration": 20
}
```

### AUDIO_DATA Message (Binary)
```
[4-byte frame length][Opus frame data]
```

### STOP Message (JSON)
```json
{"type": "stop", "session_id": "uuid4"}
```

## 2. Binary Data Format
- **Frame Header**: 4-byte big-endian integer (frame length)
- **Payload**: Raw Opus frame (20ms duration)
- Maximum frame size: 1275 bytes (Opus limit)

## 3. Metadata Requirements
Parameter     | Value  | Notes                          |
|---------------|--------|--------------------------------|
Sample Rate   | 16 kHz | Optimized for voice applications |
Channels      | Mono   | Single channel audio           |
Frame Duration| 20 ms  | Balanced latency/quality       |

## 4. Error Handling

### Error Types:
- `invalid_frame`: Frame header mismatch or decoding failure
- `network_error`: Automatic reconnect with exponential backoff
- `protocol_error`: Terminate connection
- `session_error`: Invalid session operation

### Enhanced Error Recovery:
```mermaid
sequenceDiagram
    participant C as Client
    participant S as Server
    C->>S: AUDIO_DATA (invalid)
    S->>C: ERROR (invalid_frame)
    C->>S: Reconnect after 1s
    S->>C: ACK (new session_id)
    Note right of C: Client maintains session state
```

## 5. File Saving
- **Format**: WAV files (16-bit PCM)
- **Naming**: `YYYYMMDD-HHMMSS-<session_id>.wav`
- **Storage**: Server-side only
- **Playback**: Automatic playback after session ends

## Implementation Details
1. Server: FastAPI + Uvicorn with WebSocket endpoint
2. Client: PyAudio + Opuslib with keyboard controls
3. Audio Processing: Real-time encoding/decoding
4. Session Management: UUID-based session tracking