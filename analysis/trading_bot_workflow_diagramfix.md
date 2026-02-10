graph TB
    %% =========================
    %% External Market Data
    %% =========================
    subgraph "Market Data Sources"
        WS[Pacifica WebSocket<br/>Real-time Prices]
        REST[Pacifica REST API<br/>Data Repair / Backfill ONLY]
    end

    %% =========================
    %% Data Ingestion Layer
    %% =========================
    subgraph "Data Ingestion"
        WSC[WebSocket Client<br/>pacifica_ws_client.py]
        CACHE[Price Cache<br/>In-Memory (Authoritative)]
    end

    WS --> WSC
    WSC --> CACHE
    REST -.->|"Backfill only"| CACHE

    %% =========================
    %% Core Trading Engine
    %% =========================
    subgraph "Core Engine"
        TB[Trading Bot<br/>Coordinator ONLY]
        REGIME[Market Regime Detector<br/>market_regime.py]
        STRAT[Strategy Manager<br/>Signals ONLY]
    end

    CACHE --> TB
    TB --> REGIME
    REGIME --> STRAT

    %% =========================
    %% Grid Control & Risk
    %% =========================
    subgraph "Risk & Lifecycle (Authoritative)"
        GRID[Grid Lifecycle Manager<br/>Single Grid / Symbol<br/>State Owner]
        RISK[Risk Manager<br/>Capital & Exposure Authority]
    end

    STRAT -->|"Signals"| TB
    TB --> GRID
    GRID -->|"Capital request"| RISK
    RISK -->|"Approved sizing"| GRID

    %% =========================
    %% Execution Layer
    %% =========================
    subgraph "Execution"
        CLIENT[Pacifica Client<br/>Order Execution]
    end

    GRID -->|"Orders (grid / exits)"| CLIENT
    CLIENT -->|"Fills / Positions"| GRID

    %% =========================
    %% Persistence & Monitoring
    %% =========================
    subgraph "Persistence & API"
        DB[(SQLite Database)]
        API[FastAPI Server]
    end

    CLIENT --> DB
    GRID --> DB
    TB --> DB

    API --> DB

    %% =========================
    %% Emergency & Regime Enforcement
    %% =========================
    REGIME -.->|"Regime disallowed"| GRID
    GRID -.->|"Cancel orders<br/>Flatten positions"| CLIENT

    %% =========================
    %% Styling
    %% =========================
    classDef authoritative fill:#d4edda,stroke:#155724,stroke-width:2px
    classDef coordinator fill:#fff3cd,stroke:#856404,stroke-width:2px
    classDef execution fill:#d1ecf1,stroke:#0c5460,stroke-width:2px
    classDef restricted fill:#f8d7da,stroke:#721c24,stroke-width:2px

    class CACHE,GRID,RISK authoritative
    class TB,STRAT,REGIME coordinator
    class CLIENT execution
    class REST restricted
