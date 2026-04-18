/**
 * Job queue component (active queue: pending + processing only)
 */
class JobQueue {
    constructor() {
        this.jobs = new Map();
        this.refreshInterval = null;
        this.openLogJobId = null;
        this.dragJobId = null;
    }

    async init() {
        this.setupEventListeners();
        await this.loadJobs();
        await this.loadQueueState();

        // Listen to WebSocket events
        wsClient.on('job_progress', (message) => {
            this.updateJobProgress(message.job_id, message.data);
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

        // Modal events
        const logModal = document.getElementById('log-modal');
        if (logModal) {
            logModal.addEventListener('hidden.bs.modal', () => {
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
            let message = 'Are you sure you want to force clear all jobs?';
            if (processingCount > 0) message += `\n\nThis will STOP ${processingCount} running job(s)!`;
            if (pendingCount > 0) message += `\n\n${pendingCount} pending job(s) will be cancelled.`;
            if (!confirm(message)) return;
            try {
                const cancelPromises = Array.from(this.jobs.values())
                    .filter(job => job.status === 'processing')
                    .map(job => api.deleteOrCancelJob(job.id));
                await Promise.all(cancelPromises);
                await api.clearAllJobs();
                this.jobs.clear();
                this.render();
            } catch (error) {
                console.error('Error force clearing all jobs:', error);
                alert('Failed to clear all jobs: ' + error.message);
            }
        });

        document.getElementById('btn-toggle-queue').addEventListener('click', (e) => {
            const queueElement = document.getElementById('job-queue');
            const isCollapsed = queueElement.style.display === 'none';
            queueElement.style.display = isCollapsed ? 'block' : 'none';
            e.target.textContent = isCollapsed ? 'Collapse' : 'Expand';
        });

        document.getElementById('btn-pause-queue').addEventListener('click', async () => {
            const btn = document.getElementById('btn-pause-queue');
            try {
                if (btn.textContent === 'Pause') {
                    await api.pauseQueue();
                    document.getElementById('paused-banner').classList.remove('d-none');
                    btn.textContent = 'Resume';
                    btn.classList.replace('btn-outline-warning', 'btn-outline-success');
                } else {
                    await api.resumeQueue();
                    document.getElementById('paused-banner').classList.add('d-none');
                    btn.textContent = 'Pause';
                    btn.classList.replace('btn-outline-success', 'btn-outline-warning');
                }
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
            if (state.paused) {
                banner.classList.remove('d-none');
                btn.textContent = 'Resume';
                btn.classList.replace('btn-outline-warning', 'btn-outline-success');
            } else {
                banner.classList.add('d-none');
                btn.textContent = 'Pause';
                btn.classList.replace('btn-outline-success', 'btn-outline-warning');
            }
        } catch (e) {
            console.error('Error loading queue state:', e);
        }
    }

    async loadJobs() {
        try {
            const data = await api.listJobs({ status: 'pending,processing', limit: 100, offset: 0 });
            this.jobs.clear();
            data.jobs.forEach(job => {
                this.jobs.set(job.id, job);
            });
            this.updateStats();
            this.render();
        } catch (error) {
            console.error('Error loading jobs:', error);
        }
    }

    updateStats() {
        const pending = Array.from(this.jobs.values()).filter(j => j.status === 'pending').length;
        const processing = Array.from(this.jobs.values()).filter(j => j.status === 'processing').length;
        document.getElementById('stat-pending').textContent = pending;
        document.getElementById('stat-processing').textContent = processing;
    }

    render() {
        const container = document.getElementById('job-queue');

        if (this.jobs.size === 0) {
            container.innerHTML = '<div class="text-center p-5 text-muted"><i class="bi bi-inbox fs-1 d-block mb-2"></i>No active jobs</div>';
            return;
        }

        container.innerHTML = '';

        // Sort jobs: processing first, then pending by queue_position / created_at
        const sortedJobs = Array.from(this.jobs.values()).sort((a, b) => {
            if (a.status === 'processing' && b.status !== 'processing') return -1;
            if (a.status !== 'processing' && b.status === 'processing') return 1;
            if (a.queue_position != null && b.queue_position != null) {
                return a.queue_position - b.queue_position;
            }
            return new Date(a.created_at) - new Date(b.created_at);
        });

        sortedJobs.forEach(job => {
            container.appendChild(this.createJobElement(job));
        });
    }

    encodeSummary(settings) {
        if (!settings) return '';
        const parts = [];
        if (settings.crf != null) parts.push(`CRF ${settings.crf}`);
        if (settings.encoder_preset != null || settings.preset != null) parts.push(`preset ${settings.encoder_preset ?? settings.preset}`);
        if (settings.svt_params) parts.push(settings.svt_params);
        if (settings.max_resolution) parts.push(`${settings.max_resolution}p`);
        if (settings.audio_bitrate) parts.push(settings.audio_bitrate);
        return parts.join(' · ');
    }

    createJobElement(job) {
        const element = document.createElement('div');
        element.className = 'border-bottom p-3 job-item';
        element.dataset.jobId = job.id;
        if (job.status === 'pending') {
            element.draggable = true;
            element.classList.add('cursor-move');
        }

        const fileName = job.source_file.split('/').pop();
        const presetBadge = job.preset_name_snapshot
            ? `<span class="badge bg-info text-dark me-2">[${utils.escapeHtml(job.preset_name_snapshot)}]</span>`
            : '';
        const summary = this.encodeSummary(job.settings);

        let badgeClass = 'bg-secondary';
        if (job.status === 'processing') badgeClass = 'bg-primary';

        const isPending = job.status === 'pending';
        const dragHandle = isPending ? '<i class="bi bi-grip-vertical me-2 text-muted"></i>' : '';

        element.innerHTML = `
            <div class="d-flex justify-content-between align-items-center mb-2">
                <div class="fw-medium text-truncate me-3" title="${utils.escapeHtml(job.source_file)}">
                    ${dragHandle}<i class="bi bi-film me-2 text-muted"></i>${utils.escapeHtml(fileName)}
                </div>
                <div class="d-flex align-items-center gap-2">
                    ${presetBadge}
                    <span class="badge ${badgeClass}">${job.status}</span>
                    <button class="btn btn-outline-danger btn-sm cancel-btn" data-job-id="${job.id}" title="Cancel job"><i class="bi bi-x-lg"></i></button>
                    ${(job.status !== 'pending') ? `<button class="btn btn-outline-secondary btn-sm log-btn" data-job-id="${job.id}"><i class="bi bi-file-text"></i></button>` : ''}
                </div>
            </div>
            <div class="small text-muted mb-1">${utils.escapeHtml(summary)}</div>
            <div>
                <button class="btn btn-link btn-sm p-0 text-decoration-none" data-bs-toggle="collapse" data-bs-target="#encode-details-${job.id}">Encode details</button>
                <div class="collapse mt-2" id="encode-details-${job.id}">
                    <div class="bg-body-tertiary p-2 rounded small font-monospace">
                        ${this.formatSettings(job.settings)}
                    </div>
                </div>
            </div>
            ${this.createProgressElement(job)}
        `;

        // Event listeners
        const cancelBtn = element.querySelector('.cancel-btn');
        if (cancelBtn) {
            cancelBtn.addEventListener('click', () => this.cancelJob(job.id));
        }

        const logBtn = element.querySelector('.log-btn');
        if (logBtn) {
            logBtn.addEventListener('click', () => this.showLog(job.id));
        }

        if (isPending) {
            element.addEventListener('dragstart', (e) => {
                this.dragJobId = job.id;
                e.dataTransfer.effectAllowed = 'move';
            });
            element.addEventListener('dragover', (e) => {
                e.preventDefault();
                e.dataTransfer.dropEffect = 'move';
            });
            element.addEventListener('drop', (e) => {
                e.preventDefault();
                if (this.dragJobId && this.dragJobId !== job.id) {
                    this.reorderJob(this.dragJobId, job.id);
                }
                this.dragJobId = null;
            });
        }

        return element;
    }

    formatSettings(settings) {
        if (!settings) return 'No settings';
        const lines = [];
        for (const [key, value] of Object.entries(settings)) {
            lines.push(`${utils.escapeHtml(key)}: ${utils.escapeHtml(String(value))}`);
        }
        return lines.join('<br>');
    }

    async reorderJob(draggedId, targetId) {
        // Find target position among pending jobs
        const pending = Array.from(this.jobs.values())
            .filter(j => j.status === 'pending')
            .sort((a, b) => (a.queue_position ?? Infinity) - (b.queue_position ?? Infinity));
        const targetIndex = pending.findIndex(j => j.id === targetId);
        if (targetIndex === -1) return;
        try {
            await api.moveJobPosition(draggedId, targetIndex + 1);
            await this.loadJobs();
        } catch (error) {
            window.app.showNotification(`Reorder failed: ${error.message}`, 'danger');
        }
    }

    createProgressElement(job) {
        if (job.status === 'pending') {
            return '<div class="text-muted small"><i class="bi bi-hourglass me-1"></i>Waiting in queue...</div>';
        }

        if (job.status === 'processing') {
            const percent = job.progress_percent || 0;
            const fps = job.current_fps ? job.current_fps.toFixed(1) : '0.0';
            const eta = utils.formatEta(job.eta_seconds);

            return `
                <div class="job-progress">
                    <div class="progress" style="height: 20px;">
                        <div class="progress-bar progress-bar-striped progress-bar-animated" role="progressbar"
                             style="width: ${percent}%" aria-valuenow="${percent}" aria-valuemin="0" aria-valuemax="100">
                             ${percent.toFixed(1)}%
                        </div>
                    </div>
                    <div class="d-flex justify-content-between mt-1 small text-muted">
                        <span><i class="bi bi-speedometer2 me-1"></i>${fps} FPS</span>
                        <span><i class="bi bi-clock me-1"></i>ETA: ${eta}</span>
                    </div>
                </div>
            `;
        }

        return '';
    }

    updateJobProgress(jobId, progressData) {
        const job = this.jobs.get(jobId);
        if (!job) return;

        job.progress_percent = progressData.percent;
        job.current_fps = progressData.fps;
        job.eta_seconds = progressData.eta_seconds;

        const element = document.querySelector(`[data-job-id="${jobId}"]`);
        if (element) {
            const progressContainer = element.querySelector('.job-progress');
            if (progressContainer) {
                progressContainer.outerHTML = this.createProgressElement(job);
            }
        }

        if (this.openLogJobId === jobId && progressData.current_log) {
            const logContent = document.getElementById('log-content');
            if (logContent) {
                const scrollContainer = logContent.parentElement;
                const isNearBottom = scrollContainer.scrollHeight - scrollContainer.scrollTop - scrollContainer.clientHeight < 100;
                logContent.textContent = progressData.current_log;
                if (isNearBottom) {
                    scrollContainer.scrollTop = scrollContainer.scrollHeight;
                }
            }
        }
    }

    updateJobStatus(jobId, status, error, message = {}) {
        const job = this.jobs.get(jobId);
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
            // Show toast and remove from active queue
            this.jobs.delete(jobId);
            this.render();
            this.updateStats();
            window.app.showNotification(`Job ${jobId} completed. <a href="#/history" class="alert-link" onclick="app.switchView('history')">View in History</a>`, 'success');
            return;
        }

        if (status === 'failed' || status === 'cancelled') {
            this.jobs.delete(jobId);
            this.render();
            this.updateStats();
            return;
        }

        this.updateStats();
        const element = document.querySelector(`[data-job-id="${jobId}"]`);
        if (element) {
            element.replaceWith(this.createJobElement(job));
        }
    }

    async cancelJob(jobId) {
        const job = this.jobs.get(jobId);
        const action = (job && (job.status === 'processing' || job.status === 'pending')) ? 'cancel' : 'delete';

        if (!confirm(`Are you sure you want to ${action} this job?`)) {
            return;
        }

        try {
            await api.deleteOrCancelJob(jobId);
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
            const job = await api.getJob(jobId);
            const modalElement = document.getElementById('log-modal');
            const logContent = document.getElementById('log-content');

            if (!modalElement || !logContent) {
                console.error('Modal elements not found');
                alert('Modal not found in page');
                return;
            }

            logContent.textContent = job.log || 'No log available';

            if (typeof bootstrap !== 'undefined' && bootstrap.Modal) {
                const modal = bootstrap.Modal.getOrCreateInstance(modalElement);
                modal.show();
            } else {
                console.error('Bootstrap Modal not available');
                alert('Bootstrap library not loaded properly');
            }
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
            console.log('Started fallback polling (WebSocket disconnected)');
        }
    }

    stopRefresh() {
        if (this.refreshInterval) {
            clearInterval(this.refreshInterval);
            this.refreshInterval = null;
            console.log('Stopped fallback polling (WebSocket connected)');
        }
    }
}

// Global job queue instance
const jobQueue = new JobQueue();
