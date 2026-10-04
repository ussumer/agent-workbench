package execute

import (
    "encoding/json"
    "strings"
    "testing"
    "time"
    "github.com/gorilla/websocket"
)

// Jupyter broadcasts startup and older execution messages on new channels.
// Only messages belonging to the current request may affect its result.
func TestCurrentExecutionIgnoresForeignParent(t *testing.T) {
    server := createTestServer(t, func(conn *websocket.Conn) {
        var request Message
        if err := conn.ReadJSON(&request); err != nil { return }
        foreign, _ := json.Marshal(StreamOutput{Name: StreamStdout, Text: "foreign"})
        conn.WriteJSON(Message{Header: Header{MessageType: string(MsgStream)}, ParentHeader: Header{MessageID: "older-request"}, Content: foreign})
        actual, _ := json.Marshal(StreamOutput{Name: StreamStdout, Text: "actual"})
        conn.WriteJSON(Message{Header: Header{MessageType: string(MsgStream)}, ParentHeader: request.Header, Content: actual})
        reply, _ := json.Marshal(ExecuteReply{ExecutionCount: 1})
        conn.WriteJSON(Message{Header: Header{MessageType: string(MsgExecuteReply)}, ParentHeader: request.Header, Content: reply})
        idle, _ := json.Marshal(StatusUpdate{ExecutionState: StateIdle})
        conn.WriteJSON(Message{Header: Header{MessageType: string(MsgStatus)}, ParentHeader: request.Header, Content: idle})
        time.Sleep(100 * time.Millisecond)
    })
    defer server.Close()
    client := NewClient(server.URL, nil)
    if err := client.Connect("ws" + strings.TrimPrefix(server.URL, "http") + "/api/kernels/test/channels"); err != nil { t.Fatal(err) }
    defer client.Disconnect()
    results := make(chan *ExecutionResult, 10)
    if err := client.ExecuteCodeStream("print('actual')", results); err != nil { t.Fatal(err) }
    for {
        select {
        case result := <-results:
            if result == nil { return }
            for _, item := range result.Stream {
                if item.Text != "actual" { t.Fatalf("foreign execution output accepted: %q", item.Text) }
            }
        case <-time.After(3 * time.Second): t.Fatal("current execution did not finish")
        }
    }
}

func TestReconnectUsesFreshMatchingRoutingIdentity(t *testing.T) {
    server := createTestServer(t, func(conn *websocket.Conn) { time.Sleep(10 * time.Millisecond) })
    defer server.Close()
    client := NewClient(server.URL, nil)
    endpoint := "ws" + strings.TrimPrefix(server.URL, "http") + "/api/kernels/test/channels"
    if err := client.Connect(endpoint); err != nil { t.Fatal(err) }
    first := client.session
    if !strings.Contains(client.wsURL, "session_id=" + first) { t.Fatal("websocket and header session differ") }
    client.Disconnect()
    if err := client.Connect(endpoint); err != nil { t.Fatal(err) }
    defer client.Disconnect()
    if client.session == first || !strings.Contains(client.wsURL, "session_id=" + client.session) {
        t.Fatal("reconnected channel reuses old routing identity")
    }
}

// Calling Connect for the next serial cell must retain the established channel.
func TestSerialCellsRetainContextChannel(t *testing.T) {
    server := createTestServer(t, func(conn *websocket.Conn) {
        for i := 1; i <= 5; i++ {
            var request Message
            if err := conn.ReadJSON(&request); err != nil { return }
            reply, _ := json.Marshal(ExecuteReply{ExecutionCount: i})
            conn.WriteJSON(Message{Header: Header{MessageType: string(MsgExecuteReply)}, ParentHeader: request.Header, Content: reply})
            idle, _ := json.Marshal(StatusUpdate{ExecutionState: StateIdle})
            conn.WriteJSON(Message{Header: Header{MessageType: string(MsgStatus)}, ParentHeader: request.Header, Content: idle})
        }
        time.Sleep(100 * time.Millisecond)
    })
    defer server.Close()
    client := NewClient(server.URL, nil)
    endpoint := "ws" + strings.TrimPrefix(server.URL, "http") + "/api/kernels/test/channels"
    defer client.Disconnect()
    var identity string
    for i := 0; i < 5; i++ {
        if err := client.Connect(endpoint); err != nil { t.Fatal(err) }
        if i == 0 { identity = client.session } else if identity != client.session { t.Fatal("serial cell replaced its channel") }
        results := make(chan *ExecutionResult, 10)
        if err := client.ExecuteCodeStream("1", results); err != nil { t.Fatal(err) }
        done := false
        for !done {
            select {
            case result := <-results: done = result == nil
            case <-time.After(3 * time.Second): t.Fatal("serial execution stalled")
            }
        }
    }
}

func TestReadinessRequiresMatchingShellReply(t *testing.T) {
    server := createTestServer(t, func(conn *websocket.Conn) {
        initial, _ := json.Marshal(StatusUpdate{ExecutionState: StateIdle})
        conn.WriteJSON(Message{Header: Header{MessageType: string(MsgStatus)}, Channel: "iopub", Content: initial})
        var request Message
        if err := conn.ReadJSON(&request); err != nil { return }
        if request.Header.MessageType != string(MsgKernelInfo) { t.Error("code submitted before readiness") }
        conn.WriteJSON(Message{Header: Header{MessageType: string(MsgKernelInfoReply)}, ParentHeader: Header{MessageID: "foreign"}, Content: json.RawMessage(`{}`)})
        time.Sleep(50 * time.Millisecond)
        conn.WriteJSON(Message{Header: Header{MessageType: string(MsgKernelInfoReply)}, ParentHeader: request.Header, Content: json.RawMessage(`{}`)})
        idle, _ := json.Marshal(StatusUpdate{ExecutionState: StateIdle})
        conn.WriteJSON(Message{Header: Header{MessageType: string(MsgStatus)}, ParentHeader: request.Header, Content: idle})
        time.Sleep(100 * time.Millisecond)
    })
    defer server.Close()
    client := NewClient(server.URL, nil)
    if err := client.Connect("ws" + strings.TrimPrefix(server.URL, "http") + "/api/kernels/test/channels"); err != nil { t.Fatal(err) }
    defer client.Disconnect()
    started := time.Now()
    if err := client.WaitReady(); err != nil { t.Fatal(err) }
    if time.Since(started) < 50 * time.Millisecond { t.Fatal("foreign readiness reply accepted") }
}

func TestReadinessRetriesOnlyKernelInfo(t *testing.T) {
    server := createTestServer(t, func(conn *websocket.Conn) {
        initial, _ := json.Marshal(StatusUpdate{ExecutionState: StateIdle})
        conn.WriteJSON(Message{Header: Header{MessageType: string(MsgStatus)}, Channel: "iopub", Content: initial})
        var request Message
        if err := conn.ReadJSON(&request); err != nil { return }
        first := request.Header.MessageID
        if err := conn.ReadJSON(&request); err != nil { return }
        if request.Header.MessageType != string(MsgKernelInfo) || request.Header.MessageID == first { t.Error("readiness retry submitted code or reused signature") }
        conn.WriteJSON(Message{Header: Header{MessageType: string(MsgKernelInfoReply)}, ParentHeader: request.Header, Content: json.RawMessage(`{}`)})
        idle, _ := json.Marshal(StatusUpdate{ExecutionState: StateIdle})
        conn.WriteJSON(Message{Header: Header{MessageType: string(MsgStatus)}, ParentHeader: request.Header, Content: idle})
        time.Sleep(100 * time.Millisecond)
    })
    defer server.Close()
    client := NewClient(server.URL, nil)
    if err := client.Connect("ws" + strings.TrimPrefix(server.URL, "http") + "/api/kernels/test/channels"); err != nil { t.Fatal(err) }
    defer client.Disconnect()
    if err := client.WaitReady(); err != nil { t.Fatal(err) }
}

func TestReadinessWaitsForServerSubscription(t *testing.T) {
    server := createTestServer(t, func(conn *websocket.Conn) {
        incoming := make(chan Message, 1)
        go func() { var msg Message; if conn.ReadJSON(&msg) == nil { incoming <- msg } }()
        select {
        case <-incoming: t.Error("request submitted before server subscription"); return
        case <-time.After(100 * time.Millisecond):
        }
        idle, _ := json.Marshal(StatusUpdate{ExecutionState: StateIdle})
        conn.WriteJSON(Message{Header: Header{MessageType: string(MsgStatus)}, Channel: "iopub", Content: idle})
        request := <-incoming
        conn.WriteJSON(Message{Header: Header{MessageType: string(MsgKernelInfoReply)}, ParentHeader: request.Header, Content: json.RawMessage(`{}`)})
        conn.WriteJSON(Message{Header: Header{MessageType: string(MsgStatus)}, Channel: "iopub", ParentHeader: request.Header, Content: idle})
        time.Sleep(100 * time.Millisecond)
    })
    defer server.Close()
    client := NewClient(server.URL, nil)
    if err := client.Connect("ws" + strings.TrimPrefix(server.URL, "http") + "/api/kernels/test/channels"); err != nil { t.Fatal(err) }
    defer client.Disconnect()
    if err := client.WaitReady(); err != nil { t.Fatal(err) }
}
