# S.I.M.O.N. System Diagrams

Mermaid diagrams that render on GitHub. Copy any block into a `.md` file surrounded by ` ```mermaid ` fences.

---

## System Architecture

```mermaid
graph TB
    subgraph Mac["macOS — Apple Silicon"]
        subgraph HUD["Chrome HUD (hud.html)"]
            SR[Web Speech API]
            Canvas[Neural Brain Canvas]
            Vitals[System Vitals Panel]
            ALog[Activity Log]
            Chat[Chat Box]
        end

        subgraph Core["S.I.M.O.N. Core (jarvis.py)"]
            WS[WebSocket Server :8765]
            LLM[LLM Client — Ollama API]
            Dispatch[Tool Dispatcher]
            TTS[Piper TTS]
            Stats[System Stats Collector]
        end

        subgraph KB["Knowledge Base (simon_kb.py)"]
            Contacts[(contacts\none row per person)]
            MsgCache[(messages_cache\n48h TTL)]
            Memory[(memory\npermanent facts)]
            Senders[(email_senders)]
        end

        subgraph Apple["Apple Apps — AppleScript"]
            Cal[Calendar.app]
            Mail[Mail.app]
            Msgs[Messages.app]
            Rems[Reminders.app]
        end

        subgraph Direct["Direct SQLite Reads"]
            ChatDB[chat.db WAL]
            ABDB[AddressBook .abcddb]
        end

        subgraph Scheduler["launchd"]
            AutoStart[Auto-start on login]
            HealthChecks[Health Checks 3x daily]
        end
    end

    subgraph External["External — User Configured"]
        Ollama[Ollama Endpoint\nlocal or cloud]
    end

    SR -->|voice| WS
    WS -->|JSON frames| Canvas
    WS -->|stats| Vitals
    WS -->|events| ALog
    WS -->|chunks| Chat

    WS --> Core
    LLM -->|POST /api/chat| Ollama
    Ollama -->|tool_calls + text| LLM
    Dispatch --> Apple
    Dispatch --> KB
    TTS -->|WAV| afplay[(afplay)]
    Stats --> WS

    KB --> Contacts
    KB --> MsgCache
    KB --> Memory
    KB --> Senders

    Direct --> ChatDB
    Direct --> ABDB
    Contacts -.->|synced from| ABDB
    MsgCache -.->|synced from| ChatDB

    Scheduler --> Core
```

---

## Voice Command Flow

```mermaid
sequenceDiagram
    participant User
    participant SR as Web Speech API
    participant HUD
    participant Core as jarvis.py
    participant LLM as Ollama
    participant Tool as Tool Dispatcher
    participant KB as Knowledge Base

    User->>SR: "Simon, check my messages"
    SR->>HUD: onresult (final, confident)
    HUD->>HUD: detect wake word "Simon"
    HUD->>Core: WebSocket {type:"chat", text:"check my messages"}
    Core->>HUD: {type:"thinking"}
    Note over HUD: Processing overlay appears
    Core->>LLM: POST /api/chat (stream=false, tools=[...])
    LLM->>Core: tool_calls: [get_recent_messages(hours=24)]
    Core->>HUD: {type:"tool_use", tool:"get_recent_messages"}
    Note over HUD: Overlay: ⚡ GET RECENT MESSAGES
    Core->>Tool: execute_tool(get_recent_messages)
    Tool->>KB: query_messages(hours=24, mark_read=True)
    KB->>Tool: 12 messages with resolved names
    Tool->>Core: formatted message list
    Core->>LLM: POST /api/chat (stream=true) with tool result
    LLM-->>Core: streaming response chunks
    Core-->>HUD: {type:"chunk", text:"Three messages today..."}
    Core-->>HUD: {type:"chunk", text:"...Jane said ok at eleven..."}
    Core->>HUD: {type:"done"}
    Note over HUD: Overlay hides
    Core->>TTS: synthesize_wav(response)
    TTS->>Core: WAV file
    Core->>afplay: play audio
    Core->>HUD: {type:"speech_done"}
    Note over HUD: 800ms settle window
    HUD->>HUD: state = LISTENING (green waveform)
    Note over User: SIMON waits for response
```

---

## Knowledge Base Lifecycle

```mermaid
stateDiagram-v2
    [*] --> Startup

    Startup --> ContactSync: sync_contacts()
    ContactSync --> ContactsCache: N unique persons\none row per person
    ContactsCache --> [*]: Permanent until next sync

    Startup --> MessageSync: sync_messages(hours_back=48)
    MessageSync --> MessagesCache: New messages only\n(msg_rowid dedup)
    MessagesCache --> QueryMessages: query_messages()
    QueryMessages --> Marked: read_by_simon = 1
    Marked --> Cleared: run_maintenance()

    MessagesCache --> Expired: expires_at <= now
    Expired --> Cleared: run_maintenance()
    Cleared --> [*]

    note right of MessagesCache
        48h TTL
        Short-lived buffer
        Not permanent storage
    end note

    Startup --> Maintenance: run_maintenance()
    Maintenance --> IntegrityCheck
    Maintenance --> ExpireMessages
    Maintenance --> ClearRead
    Maintenance --> DedupeContacts
    Maintenance --> PruneSessions
    Maintenance --> Vacuum

    state "Every 6 hours" as BG {
        [*] --> SyncMessages10min
        SyncMessages10min --> SyncMessages10min: every 10 min
        [*] --> Maintenance6h
        Maintenance6h --> Maintenance6h: every 6 hours
    }
```

---

## HUD State Machine

```mermaid
stateDiagram-v2
    [*] --> sleeping: page load

    sleeping --> wake: WebSocket connected
    wake --> listening: wake word detected
    listening --> processing: final speech received
    processing --> speaking: response streaming
    speaking --> listening: speech_done + 800ms settle\n(conversation mode)
    speaking --> wake: speech_done\n(non-conversation)

    wake --> muted: MUTE button / brain click
    muted --> wake: UNMUTE button / brain click

    listening --> wake: kill phrase detected
    wake --> sleeping: WebSocket disconnected

    note right of listening
        Green waveform
        awake = true
    end note

    note right of processing
        Gold brain
        Processing overlay active
    end note

    note right of speaking
        Blue brain
        TTS playing
        SR results discarded
    end note
```
