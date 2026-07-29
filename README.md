# Ms-Forms C2

## Introduction

This project demonstrates a novel approach to building a Command and Control (C2) infrastructure by leveraging Microsoft Forms as the primary communication medium. Unlike traditional C2 frameworks that require dedicated servers, complex infrastructure, and custom protocols, this solution uses a ubiquitous and legitimate Microsoft cloud service to establish bidirectional communication with compromised endpoints.

```mermaid
graph TB
    subgraph endpoints["COMPROMISED ENDPOINTS"]
        host1["Windows Host 1<br/>(Client: PowerShell)"]
        host2["Windows Host 2<br/>(Client: PowerShell)"]
        hostn["... N Hosts<br/>(Client: PowerShell)"]
    end
    
    subgraph cloud["MICROSOFT CLOUD INFRASTRUCTURE"]
        forms["Microsoft Forms<br/>(Response Repository)<br/>- Questions<br/>- Responses<br/>- Title Field"]
    end
    
    subgraph operator["OPERATOR'S MACHINE"]
        server["Server (Python)<br/>- Monitors responses<br/>- Issues commands<br/>- Interactive console"]
    end
    
    host1 -->|HTTPS/TLS Submit & Poll| forms
    host2 -->|HTTPS/TLS Submit & Poll| forms
    hostn -->|HTTPS/TLS Submit & Poll| forms
    
    forms -->|Read responses| server
    server -->|Modify title| forms
    
    style endpoints fill:#e8e8e8,stroke:#333,color:#000
    style cloud fill:#f5f5f5,stroke:#333,color:#000
    style operator fill:#e8e8e8,stroke:#333,color:#000
    style forms fill:#f9f9f9,stroke:#333,color:#000
```

---

## Setup

### Creating the Microsoft Form

Before running the C2 framework, you must create a Microsoft Form with the exact specifications below. This form will serve as your centralized communication hub.

#### Form Configuration Requirements

1. **Access Level**
   - The form must be PUBLIC (anyone with the link can access)
   - This allows both the client and server to submit and retrieve data without authentication complications

2. **Form Title**
   - The form must have exactly ONE title field
   - This title is used for issuing commands from the operator to clients
   - Format: `[UUID] COMMAND` (e.g., `[3f9a1c2b] whoami`)

3. **Questions (Fields)**
   - The form must have exactly NINE (9) questions
   - ALL questions must be of type "Text" (single-line text input)

![Microsoft Form Configuration](images/form.png)

---

## Workflow Examples

### Example 1: Initial Connection and Host Detection

![Workflow Example 1](images/1.png)

### Example 2: Command Execution and Response Collection

![Workflow Example 2](images/2.png)

### Example 3: Multi-Host Management

![Workflow Example 3](images/3.png)

---

## License & Disclaimer

This project is provided for **authorized security testing and educational purposes only**. Unauthorized access to computer systems is illegal. Users are responsible for ensuring all use is legal and authorized.

- Do not use on systems you don't own or have explicit permission to test
- Do not use for malicious purposes
- Follow all applicable laws and regulations
- Report security findings responsibly to affected organizations

Author & Research: @xpsecsecurity