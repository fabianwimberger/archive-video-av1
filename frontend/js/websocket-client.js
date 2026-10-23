class WebSocketClient {
    constructor() {
        this.ws = null;
        this.reconnectInterval = 1000;
        this.maxReconnectInterval = 30000;
        this.currentReconnectInterval = this.reconnectInterval;
        this.listeners = new Map();
        this.connected = false;
    }

    connect() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws`;

        console.log('Connecting to WebSocket:', wsUrl);
        this.updateStatus('connecting');

        this.ws = new WebSocket(wsUrl);

        this.ws.onopen = () => {
            console.log('WebSocket connected');
            this.connected = true;
            this.currentReconnectInterval = this.reconnectInterval;
            this.updateStatus('connected');
            this.emit('connected');
        };

        this.ws.onmessage = (event) => {
            try {
                const message = JSON.parse(event.data);
                this.handleMessage(message);
            } catch (error) {
                console.error('Error parsing WebSocket message:', error);
            }
        };

        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
        };

        this.ws.onclose = () => {
            console.log('WebSocket disconnected');
            this.connected = false;
            this.updateStatus('disconnected');
            this.emit('disconnected');
            this.scheduleReconnect();
        };
    }

    scheduleReconnect() {
        console.log(`Reconnecting in ${this.currentReconnectInterval}ms...`);
        setTimeout(() => {
            this.connect();
            this.currentReconnectInterval = Math.min(
                this.currentReconnectInterval * 2,
                this.maxReconnectInterval
            );
        }, this.currentReconnectInterval);
    }

    handleMessage(message) {
        const { type } = message;

        switch (type) {
            case 'job_progress':
                this.emit('job_progress', message);
                break;
            case 'job_status':
                this.emit('job_status', message);
                break;
            case 'queue_update':
                this.emit('queue_update', message);
                break;
            case 'system':
                this.emit('system', message);
                break;
            case 'pong':
                // Heartbeat response
                break;
            default:
                console.warn('Unknown message type:', type);
        }
    }

    send(data) {
        if (this.connected && this.ws) {
            this.ws.send(JSON.stringify(data));
        }
    }

    on(event, callback) {
        if (!this.listeners.has(event)) {
            this.listeners.set(event, []);
        }
        this.listeners.get(event).push(callback);
    }

    emit(event, data) {
        const callbacks = this.listeners.get(event);
        if (callbacks) {
            callbacks.forEach(callback => callback(data));
        }
    }

    updateStatus(status) {
        const statusElement = document.getElementById('ws-status');
        if (!statusElement) return;

        const labels = {
            connected: ['status--ok', 'Connected'],
            connecting: ['status--warn', 'Connecting'],
            disconnected: ['status--fail', 'Disconnected'],
        };
        const [modifier, label] = labels[status] || ['status--info', status];
        statusElement.className = `status ${modifier}`;
        statusElement.textContent = label;
    }

    disconnect() {
        if (this.ws) {
            this.ws.close();
        }
    }
}

const wsClient = new WebSocketClient();
