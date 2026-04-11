import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { AgentWebSocket } from './websocket'
import { ConnectionStatus } from '@/types/agent'

describe('AgentWebSocket reconnect backoff logic', () => {
  let mockWebSocket: any;
  let webSocketInstances: any[] = [];
  
  beforeEach(() => {
    vi.useFakeTimers()
    vi.spyOn(Math, 'random').mockReturnValue(1)
    webSocketInstances = [];
    
    // Mock the global WebSocket
    class MockWebSocket {
      url: string;
      readyState: number;
      send: any;
      close: any;
      onopen?: () => void;
      onclose?: (event: any) => void;
      constructor(url: string) {
        this.url = url;
        this.readyState = 0; // CONNECTING
        this.send = vi.fn();
        this.close = vi.fn();
        
        mockWebSocket = this;
        webSocketInstances.push(this);
      }
    }
    
    global.WebSocket = MockWebSocket as any;

    // For environment variable defaults in AgentWebSocket class property initializers
    Object.defineProperty(window, 'location', {
      value: { protocol: 'http:', host: 'localhost' },
      writable: true
    });
  })

  afterEach(() => {
    vi.runOnlyPendingTimers()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('preserves exponential backoff when connections drop instantaneously', () => {
    const agentWS = new AgentWebSocket();
    
    const statusChanges: ConnectionStatus[] = [];
    agentWS.onStatus((status) => statusChanges.push(status));

    agentWS.connect();
    
    // Attempt 1: Connects
    expect(webSocketInstances.length).toBe(1);
    
    // Simulate instantaneous close following an open
    mockWebSocket.readyState = 1; // OPEN
    if (mockWebSocket.onopen) mockWebSocket.onopen();
    
    // Connection drops in less than 3000ms (e.g. 10ms later)
    vi.advanceTimersByTime(10);
    if (mockWebSocket.onclose) mockWebSocket.onclose({ code: 1006, reason: 'Abnormal drop', wasClean: false });
    
    // Reconnect timer is scheduled (attempt = 1) -> base delay is 1000ms. With max jitter (1.2), this is up to 1200ms.
    vi.advanceTimersByTime(1200);
    expect(webSocketInstances.length).toBe(2);
    
    const ws2 = webSocketInstances[1];
    ws2.readyState = 1;
    if (ws2.onopen) ws2.onopen();
    
    // Drops immediately again within STABLE_CONNECTION_MS threshold
    vi.advanceTimersByTime(10);
    if (ws2.onclose) ws2.onclose({ code: 1006, reason: 'Abnormal drop', wasClean: false });
    
    // Attempt 2 reconnect timer is scheduled -> base delay applies exponential multiplier (Math.pow(1.6, 2-1)) = ~1600ms
    // Max jitter is 1600 * 1.2 = 1920ms.
    vi.advanceTimersByTime(1500); // Hasn't fired
    expect(webSocketInstances.length).toBe(2); 

    // Advance enough to guarantee fire
    vi.advanceTimersByTime(500);
    expect(webSocketInstances.length).toBe(3);
    
    const ws3 = webSocketInstances[2];
    ws3.readyState = 1;
    if (ws3.onopen) ws3.onopen();
    
    // Drops immediately again
    vi.advanceTimersByTime(10);
    if (ws3.onclose) ws3.onclose({ code: 1006, reason: 'Abnormal drop', wasClean: false });
    
    // Attempt 3 reconnect timer is scheduled (Math.pow(1.6, 3-1)) = ~2560ms
    // Max jitter is 2560 * 1.2 = 3072ms.
    vi.advanceTimersByTime(2000);
    expect(webSocketInstances.length).toBe(3); // Hasn't reconnected yet
    vi.advanceTimersByTime(1100);
    expect(webSocketInstances.length).toBe(4);
  })

  it('resets exponential backoff after a stable connection duration', () => {
    const agentWS = new AgentWebSocket();
    agentWS.connect();
    
    expect(webSocketInstances.length).toBe(1);
    
    // Attempt 1: Connects
    mockWebSocket.readyState = 1;
    if (mockWebSocket.onopen) mockWebSocket.onopen();
    
    // Connection drops in less than 3000ms 
    vi.advanceTimersByTime(10);
    if (mockWebSocket.onclose) mockWebSocket.onclose({ code: 1006, reason: '', wasClean: false });
    
    // 1st Reconnect
    vi.advanceTimersByTime(1200);
    expect(webSocketInstances.length).toBe(2);
    
    const ws2 = webSocketInstances[1];
    ws2.readyState = 1;
    if (ws2.onopen) ws2.onopen();
    
    // STABLE CONNECTION! It survives for 4000ms
    vi.advanceTimersByTime(4000);
    if (ws2.onclose) ws2.onclose({ code: 1006, reason: '', wasClean: false });
    
    // Because it survived longer than STABLE_CONNECTION_MS (3000ms), 
    // the backoff is reset. Attempt is treated as 1, which means 1000ms base wait. Max jitter = 1200ms.
    vi.advanceTimersByTime(1200);
    expect(webSocketInstances.length).toBe(3); // Should immediately reconnect without large multiplier
  })
})
