/**
 * Job queue component (active queue: pending + processing only)
 */
class JobQueue {
    constructor() {
        this.jobs = new Map();
        this.refreshInterval = null;
        this.clusterInterval = null;
        this.openLogJobId = null;
        this.dragJobId = null;
        this.clusterStatus = null;
    }

    async init() {
        this.setupEventListeners();
        await this.loadJobs();
        await this.loadQueueState();
        await this.loadClusterStatus();
        this.startClusterRefresh();

        wsClient.on('job_progress', (message) => {
            this.updateJobProgress(message.job_id, message.data, message);
        });

        wsClient.on('job_status', (message) => {
            this.updateJobStatus(message.job_id, message.status, message.error, message);
        });

        wsClient.on('queue_update', () => {
            this.loadJobs();
            this.loadQueueState();
        });

        wsClient.on('connected', () => {
            this.stopRefresh();
        });

        wsClient.on('disconnected', () => {
            this.startRefresh();
        });

        const logDialog = document.getElementById('log-modal');
        if (logDialog) {
            logDialog.addEventListener('close', () => {
                this.openLogJobId = null;
            });
        }
    }

    setupEventListeners() {
        document.getElementById('btn-clear-queued').addEventListener('click', async () => {
            const pendingCount = Array.from(this.jobs.values()).filter(j => j.status === 'pending').length;
            if (pendingCount === 0) return;
            if (!confirm(`Clear ${pendingCount} queued job(s)?`)) return;
            try {
                await api.clearQueuedJobs();
                await this.loadJobs();
            } catch (error) {
                console.error('Error clearing queued jobs:', error);
                alert('Failed to clear queued jobs');
            }
        });

        document.getElementById('btn-clear-all').addEventListener('click', async () => {
            if (this.jobs.size === 0) return;
            const processingCount = Array.from(this.jobs.values()).filter(j => j.status === 'processing').length;
            const pendingCount = Array.from(this.jobs.values()).filter(j => j.status === 'pending').length;
            let message = 'Clear all jobs?';
            if (processingCount > 0) message += `\n\nThis stops ${processingCount} running job(s).`;
            if (pendingCount > 0) message += `\n\n${pendingCount} waiting job(s) will be cancelled.`;
            if (!confirm(message)) return;
            try {
                const cancelPromises = Array.from(this.jobs.values())
                    .filter(job => job.status === 'processing')
                    .map(job => api.deleteOrCancelJob(job.id, job.cluster_node_id));
                await Promise.all(cancelPromises);
                await api.clearAllJobs();
                this.jobs.clear();
                this.render();
                this.updateStats();
            } catch (error) {
                console.error('Error force clearing all jobs:', error);
                alert('Failed to clear all jobs: ' + error.message);
            }
        });

        document.getElementById('btn-pause-queue').addEventListener('click', async () => {
            const btn = document.getElementById('btn-pause-queue');
            try {
                if (btn.textContent === 'Pause') {
                    await api.pauseQueue();
                } else {
                    await api.resumeQueue();
                }
                await this.loadQueueState();
            } catch (error) {
                window.app.showNotification(`Queue control failed: ${error.message}`, 'danger');
            }
        });
    }

    async loadQueueState() {
        try {
            const state = await api.getQueueState();
            const banner = document.getElementById('paused-banner');
            const btn = document.getElementById('btn-pause-queue');
            const statusPaused = document.getElementById('status-paused');

            banner.hidden = !state.paused;
            statusPaused.hidden = !state.paused;
            btn.textContent = state.paused ? 'Resume' : 'Pause';
        } catch (e) {
            console.error('Error loading queue state:', e);
        }
    }

    async loadClusterStatus() {
        const container = document.getElementById('cluster-status');
        if (!container) return;

        try {
            this.clusterStatus = await api.getClusterStatus();
            this.renderClusterStatus();
        } catch (error) {
            console.error('Error loading cluster status:', error);
            container.innerHTML = '<p class="muted small">Cluster status unavailable</p>';
            document.getElementById('status-cluster').textContent = 'Cluster unavailable';
        }
    }

    clusterRole(status) {
        if (!status.enabled) return 'Standalone';
        if (status.is_leader) return 'Leader';
        if (status.leader_url) return 'Follower';
        return 'Self-led';
    }

    renderClusterStatus() {
        const container = document.getElementById('cluster-status');
        if (!container || !this.clusterStatus) return;

        const status = this.clusterStatus;
        const peers = status.peers || [];
        const role = this.clusterRole(status);

        const peerItems = peers.length
            ? peers.map(peer => `
                <li>
                    <span>${utils.escapeHtml(peer.node_name)}</span>
                    <span class="num">${Number(peer.last_seen_seconds).toFixed(1)} s ago</span>
                </li>
            `).join('')
            : '<li><span class="muted">No peers discovered</span></li>';

        container.innerHTML = `
            <div class="cluster-grid">
                <section class="cluster-block">
                    <h2>This node</h2>
                    <dl class="detail-list">
                        <dt>Name</dt><dd>${utils.escapeHtml(status.node_name)}</dd>
                        <dt>Node ID</dt><dd class="machine">${utils.escapeHtml(status.node_id)}</dd>
                        <dt>Role</dt><dd><span class="status ${status.enabled ? 'status--ok' : 'status--info'}">${role}</span></dd>
                        <dt>Address</dt><dd class="machine">${utils.escapeHtml(status.public_url || '—')}</dd>
                        <dt>Leader</dt><dd class="machine">${utils.escapeHtml(status.leader_url || '—')}</dd>
                    </dl>
                </section>
                <section class="cluster-block">
                    <h2>Peers <span class="num">${peers.length}</span></h2>
                    <ul class="cluster-list">${peerItems}</ul>
                </section>
            </div>
            <p class="field__hint">Settings are per node. Delegated jobs run with the worker's own track, language and ownership settings.</p>
        `;

        document.getElementById('rail-peer-count').textContent = String(peers.length);
        document.getElementById('status-node').textContent = status.node_name;
        document.getElementById('status-role').textContent = role.toLowerCase();
        document.getElementById('status-peers').textContent = String(peers.length);
        document.getElementById('status-cluster').textContent = status.enabled
            ? `Cluster on, ${peers.length} peer${peers.length === 1 ? '' : 's'}`
            : 'Cluster off';
    }

    async loadJobs() {
        try {
            const data = await api.listJobs({ status: 'pending,processing', limit: 100, offset: 0, order: 'asc' });
            this.jobs.clear();
            data.jobs.forEach(job => {
                this.jobs.set(this.jobKey(job), job);
            });
            this.updateStats();
            this.render();
        } catch (error) {
            console.error('Error loading jobs:', error);
        }
    }

    jobKey(job) {
        return job.cluster_job_id || `${job.cluster_node_id || 'local'}:${job.id}`;
    }

    updateStats() {
        const pending = Array.from(this.jobs.values()).filter(j => j.status === 'pending').length;
        const processing = Array.from(this.jobs.values()).filter(j => j.status === 'processing').length;

        document.getElementById('stat-pending').textContent = pending;
        document.getElementById('stat-processing').textContent = processing;
        document.getElementById('rail-queue-count').textContent = String(pending);
        document.getElementById('status-running').textContent = processing;
        document.getElementById('status-waiting').textContent = pending;
    }

    sortedJobs() {
        return Array.from(this.jobs.values()).sort((a, b) => {
            if (a.status === 'processing' && b.status !== 'processing') return -1;
            if (a.status !== 'processing' && b.status === 'processing') return 1;
            if (a.queue_position != null && b.queue_position != null) {
                return a.queue_position - b.queue_position;
            }
            return new Date(a.created_at) - new Date(b.created_at);
        });
    }

    render() {
        const container = document.getElementById('job-queue');

        if (this.jobs.size === 0) {
            container.innerHTML = '<p class="empty">No active jobs</p>';
            this.renderLiveStrip();
            return;
        }

        container.replaceChildren();
        this.sortedJobs().forEach(job => {
            container.appendChild(this.createJobElement(job));
        });

        this.renderLiveStrip();
    }

    renderLiveStrip() {
        const current = document.getElementById('live-current');
        const next = document.getElementById('live-next');
        if (!current || !next) return;

        const sorted = this.sortedJobs();
        const running = sorted.find(job => job.status === 'processing');
        const waiting = sorted.filter(job => job.status === 'pending').slice(0, 3);

        if (!running) {
            current.innerHTML = '<p class="empty">No job running</p>';
        } else {
            const percent = Number(running.progress_percent || 0);
            const fps = running.current_fps ? running.current_fps.toFixed(1) : '0.0';
            current.innerHTML = `
                <div class="live__job-row">
                    <span class="live__job-name" title="${utils.escapeHtml(running.source_file)}">${utils.escapeHtml(running.source_file.split('/').pop())}</span>
                    <span class="status status--run num">${percent.toFixed(0)}%</span>
                </div>
                <div class="progress"><span style="width: ${Math.min(percent, 100)}%"></span></div>
                <div class="live__job-meta">
                    <span>${utils.escapeHtml(this.encodeSummary(running.settings) || 'Encoder defaults')}</span>
                    <span class="num">${fps} fps, ETA ${running.eta_seconds ? utils.formatEta(running.eta_seconds) : 'unknown'}</span>
                </div>
            `;
        }

        if (waiting.length === 0) {
            next.innerHTML = '<li><span class="muted">Nothing waiting</span></li>';
        } else {
            next.innerHTML = waiting.map(job => `
                <li>
                    <span title="${utils.escapeHtml(job.source_file)}">${utils.escapeHtml(job.source_file.split('/').pop())}</span>
                    <span class="num">${job.queue_position != null ? `#${job.queue_position}` : 'waiting'}</span>
                </li>
            `).join('');
        }
    }

    encodeSummary(settings) {
        if (!settings) return '';
        const parts = [];
        if (settings.crf != null) parts.push(`CRF ${settings.crf}`);
        if (settings.encoder_preset != null || settings.preset != null) parts.push(`speed ${settings.encoder_preset ?? settings.preset}`);
        if (settings.svt_params) parts.push(settings.svt_params);
        if (settings.max_resolution) parts.push(`${settings.max_resolution}p`);
        if (settings.audio_bitrate) parts.push(settings.audio_bitrate);
        return parts.join(', ');
    }

    statusBadge(job) {
        if (job.status === 'processing') return '<span class="status status--run">Encoding</span>';
        if (job.status === 'pending') return '<span class="status status--info">Waiting</span>';
        if (job.status === 'failed') return '<span class="status status--fail">Failed</span>';
        if (job.status === 'cancelled') return '<span class="status status--warn">Cancelled</span>';
        return `<span class="status status--ok">${utils.escapeHtml(job.status)}</span>`;
    }

    createJobElement(job) {
        const element = document.createElement('article');
        element.className = 'job-item';
        const key = this.jobKey(job);
        element.dataset.jobId = key;
        element.dataset.status = job.status;

        if (job.status === 'pending') {
            element.draggable = true;
            element.title = 'Drag to reorder';
        }

        const fileName = job.source_file.split('/').pop();
        const preset = job.preset_name_snapshot
            ? `<span class="small muted">${utils.escapeHtml(job.preset_name_snapshot)}</span>`
            : '';
        const workerName = job.assigned_worker_name || job.assigned_worker_id;
        const worker = workerName
            ? `<span class="small muted">on ${utils.escapeHtml(workerName)}</span>`
            : '';
        const isPending = job.status === 'pending';
        const grip = isPending ? '<span class="grip" aria-hidden="true">⋮⋮</span>' : '';

        element.innerHTML = `
            <div class="job-item__head">
                <span class="job-item__name">${grip}${utils.escapeHtml(fileName)}</span>
                <span class="job-item__actions">
                    ${worker}
                    ${preset}
                    ${this.statusBadge(job)}
                    <button class="btn btn--quiet btn--small cancel-btn" type="button" title="Cancel job">Cancel</button>
                    ${job.status !== 'pending' ? '<button class="btn btn--text btn--small log-btn" type="button">Log</button>' : ''}
                </span>
            </div>
            <p class="job-item__summary">${utils.escapeHtml(this.encodeSummary(job.settings) || 'Encoder defaults')}</p>
            <details class="job-item__details">
                <summary>Encode details</summary>
                ${this.formatSettings(job.settings)}
            </details>
            ${this.createProgressElement(job)}
        `;

        const cancelBtn = element.querySelector('.cancel-btn');
        if (cancelBtn) {
            cancelBtn.addEventListener('click', () => this.cancelJob(key));
        }

        const logBtn = element.querySelector('.log-btn');
        if (logBtn) {
            logBtn.addEventListener('click', () => this.showLog(key));
        }

        if (isPending) {
            element.addEventListener('dragstart', (e) => {
                this.dragJobId = key;
                e.dataTransfer.effectAllowed = 'move';
            });
            element.addEventListener('dragover', (e) => {
                e.preventDefault();
                e.dataTransfer.dropEffect = 'move';
            });
            element.addEventListener('drop', (e) => {
                e.preventDefault();
                if (this.dragJobId && this.dragJobId !== key) {
                    this.reorderJob(this.dragJobId, key);
                }
                this.dragJobId = null;
            });
        }

        return element;
    }

    formatSettings(settings) {
        if (!settings) return '<p class="job-item__summary">No settings recorded</p>';
        const rows = Object.entries(settings)
            .map(([key, value]) => `<dt>${utils.escapeHtml(key)}</dt><dd>${utils.escapeHtml(String(value))}</dd>`)
            .join('');
        return `<dl>${rows}</dl>`;
    }

    async reorderJob(draggedId, targetId) {
        const pending = Array.from(this.jobs.values())
            .filter(j => j.status === 'pending')
            .sort((a, b) => (a.queue_position ?? Infinity) - (b.queue_position ?? Infinity));
        const targetIndex = pending.findIndex(j => this.jobKey(j) === targetId);
        if (targetIndex === -1) return;
        try {
            const dragged = this.jobs.get(draggedId);
            await api.moveJobPosition(dragged.id, targetIndex + 1, dragged.cluster_node_id);
            await this.loadJobs();
        } catch (error) {
            window.app.showNotification(`Reorder failed: ${error.message}`, 'danger');
        }
    }

    createProgressElement(job) {
        if (job.status === 'pending') {
            return '<p class="job-item__summary">Waiting in queue</p>';
        }

        if (job.status === 'processing') {
            const percent = Number(job.progress_percent || 0);
            const fps = job.current_fps ? job.current_fps.toFixed(1) : '0.0';
            const eta = job.eta_seconds ? utils.formatEta(job.eta_seconds) : 'unknown';

            return `
                <div class="job-progress">
                    <div class="progress"><span style="width: ${Math.min(percent, 100)}%"></span></div>
                    <div class="job-item__summary num">${percent.toFixed(1)}%, ${fps} fps, ETA ${eta}</div>
                </div>
            `;
        }

        return '';
    }

    resolveDisplayedJobId(jobId, message = {}) {
        if (message.cluster_job_id && this.jobs.has(message.cluster_job_id)) return message.cluster_job_id;
        for (const job of this.jobs.values()) {
            if (message.node_id && (
                (job.cluster_node_id === message.node_id && Number(job.id) === Number(jobId)) ||
                (job.assigned_worker_id === message.node_id && Number(job.remote_job_id) === Number(jobId))
            )) {
                return this.jobKey(job);
            }
        }
        return null;
    }

    updateJobProgress(jobId, progressData, message = {}) {
        const displayedJobId = this.resolveDisplayedJobId(jobId, message);
        const job = this.jobs.get(displayedJobId);
        if (!job) return;

        job.progress_percent = progressData.percent;
        job.current_fps = progressData.fps;
        job.eta_seconds = progressData.eta_seconds;

        const element = document.querySelector(`.job-item[data-job-id="${CSS.escape(displayedJobId)}"]`);
        if (element) {
            const progressContainer = element.querySelector('.job-progress');
            if (progressContainer) {
                progressContainer.outerHTML = this.createProgressElement(job);
            }
        }

        this.renderLiveStrip();

        if (this.openLogJobId === displayedJobId && progressData.current_log) {
            const logContent = document.getElementById('log-content');
            if (logContent) {
                const isNearBottom = logContent.scrollHeight - logContent.scrollTop - logContent.clientHeight < 100;
                logContent.textContent = progressData.current_log;
                if (isNearBottom) {
                    logContent.scrollTop = logContent.scrollHeight;
                }
            }
        }
    }

    updateJobStatus(jobId, status, error, message = {}) {
        const displayedJobId = this.resolveDisplayedJobId(jobId, message);
        const job = this.jobs.get(displayedJobId);
        if (!job) {
            this.loadJobs();
            return;
        }

        job.status = status;
        job.error_message = error;

        if (status === 'completed') {
            job.progress_percent = 100;
            if (message.source_size_bytes != null) job.source_size_bytes = message.source_size_bytes;
            if (message.output_size_bytes != null) job.output_size_bytes = message.output_size_bytes;
            this.jobs.delete(displayedJobId);
            this.render();
            this.updateStats();
            window.app.showNotification(`Job ${job.id} completed. View it in History.`, 'success');
            return;
        }

        if (status === 'failed' || status === 'cancelled') {
            this.jobs.delete(displayedJobId);
            this.render();
            this.updateStats();
            return;
        }

        this.updateStats();
        const element = document.querySelector(`.job-item[data-job-id="${CSS.escape(displayedJobId)}"]`);
        if (element) {
            element.replaceWith(this.createJobElement(job));
        }
        this.renderLiveStrip();
    }

    async cancelJob(jobId) {
        const job = this.jobs.get(jobId);
        const action = (job && (job.status === 'processing' || job.status === 'pending')) ? 'cancel' : 'delete';

        if (!confirm(`Are you sure you want to ${action} this job?`)) {
            return;
        }

        try {
            await api.deleteOrCancelJob(job.id, job.cluster_node_id);
            this.jobs.delete(jobId);
            this.render();
            this.updateStats();
        } catch (error) {
            console.error(`Error ${action}ing job:`, error);
            alert(`Failed to ${action} job: ${error.message}`);
        }
    }

    async showLog(jobId) {
        try {
            this.openLogJobId = jobId;
            const displayed = this.jobs.get(jobId);
            const job = await api.getJob(displayed.id, displayed.cluster_node_id);
            const dialog = document.getElementById('log-modal');
            const logContent = document.getElementById('log-content');

            if (!dialog || !logContent) {
                console.error('Log dialog elements not found');
                alert('Log dialog not found in page');
                return;
            }

            logContent.textContent = job.log || 'No log available';
            dialog.showModal();
        } catch (error) {
            console.error('Error fetching job log:', error);
            alert(`Failed to load job log: ${error.message}`);
            this.openLogJobId = null;
        }
    }

    startRefresh() {
        if (!this.refreshInterval) {
            this.refreshInterval = setInterval(() => {
                this.loadJobs();
                this.loadQueueState();
            }, 5000);
        }
    }

    stopRefresh() {
        if (this.refreshInterval) {
            clearInterval(this.refreshInterval);
            this.refreshInterval = null;
        }
    }

    startClusterRefresh() {
        if (!this.clusterInterval) {
            this.clusterInterval = setInterval(() => {
                this.loadClusterStatus();
                this.loadJobs();
            }, 5000);
        }
    }
}

const jobQueue = new JobQueue();
