/**
 * Job queue component
 */
class JobQueue {
    constructor() {
        this.jobs = new Map();
        this.refreshInterval = null;
        this.openLogJobId = null;
    }

    async init() {
        this.setupEventListeners();
        await this.loadJobs();
        // Don't start polling initially - will start only if WebSocket disconnects

        // Listen to WebSocket events
        wsClient.on('job_progress', (message) => {
            this.updateJobProgress(message.job_id, message.data);
        });

        wsClient.on('job_status', (message) => {
            this.updateJobStatus(message.job_id, message.status, message.error);
        });

        wsClient.on('queue_update', () => {
            this.loadJobs();
            this.updateStats();
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
        document.getElementById('btn-clear-completed').addEventListener('click', async () => {
            try {
                await api.clearCompletedJobs();
                await this.loadJobs();
            } catch (error) {
                console.error('Error clearing completed jobs:', error);
                alert('Failed to clear completed jobs');
            }
        });

        document.getElementById('btn-clear-all').addEventListener('click', async () => {
            if (this.jobs.size === 0) return;

            const processingCount = Array.from(this.jobs.values()).filter(j => j.status === 'processing').length;
            const pendingCount = Array.from(this.jobs.values()).filter(j => j.status === 'pending').length;

            let message = 'Are you sure you want to force clear all jobs?';
            if (processingCount > 0) {
                message += `\n\nThis will STOP ${processingCount} running job(s)!`;
            }
            if (pendingCount > 0) {
                message += `\n\n${pendingCount} pending job(s) will be cancelled.`;
            }

            if (!confirm(message)) {
                return;
            }

            try {
                // Cancel all processing jobs first
                const cancelPromises = Array.from(this.jobs.values())
                    .filter(job => job.status === 'processing')
                    .map(job => api.deleteOrCancelJob(job.id));

                await Promise.all(cancelPromises);

                // Then clear all jobs from database
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
    }

    async loadJobs() {
        try {
            const data = await api.listJobs(null, 100, 0);
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
        const completed = Array.from(this.jobs.values()).filter(j => j.status === 'completed').length;
        const failed = Array.from(this.jobs.values()).filter(j => j.status === 'failed').length;

        document.getElementById('stat-pending').textContent = pending;
        document.getElementById('stat-processing').textContent = processing;
        document.getElementById('stat-completed').textContent = completed;
        document.getElementById('stat-failed').textContent = failed;
    }

    render() {
        const container = document.getElementById('job-queue');

        if (this.jobs.size === 0) {
            container.innerHTML = '<div class="text-center p-5 text-muted"><i class="bi bi-inbox fs-1 d-block mb-2"></i>No jobs yet</div>';
            return;
        }

        container.innerHTML = '';

        // Sort jobs: processing first, then pending, then others by created_at asc (oldest first)
        const sortedJobs = Array.from(this.jobs.values()).sort((a, b) => {
            const statusOrder = { processing: 0, pending: 1, completed: 2, failed: 2, cancelled: 2 };
            if (statusOrder[a.status] !== statusOrder[b.status]) {
                return statusOrder[a.status] - statusOrder[b.status];
            }
            return new Date(a.created_at) - new Date(b.created_at);
        });

        sortedJobs.forEach(job => {
            container.appendChild(this.createJobElement(job));
        });
    }

    createJobElement(job) {
        const element = document.createElement('div');
        element.className = 'border-bottom p-3 job-item';
        element.dataset.jobId = job.id;

        const fileName = job.source_file.split('/').pop();
        
        let badgeClass = 'bg-secondary';
        if (job.status === 'processing') badgeClass = 'bg-primary';
        if (job.status === 'completed') badgeClass = 'bg-success';
        if (job.status === 'failed') badgeClass = 'bg-danger';
        if (job.status === 'cancelled') badgeClass = 'bg-secondary';

        const isFinished = ['completed', 'failed', 'cancelled'].includes(job.status);
        const buttonIcon = isFinished ? 'bi-trash' : 'bi-x-lg';
        const buttonTitle = isFinished ? 'Delete job from history' : 'Cancel job';

        element.innerHTML = `
            <div class="d-flex justify-content-between align-items-center mb-2">
                <div class="fw-medium text-truncate me-3" title="${utils.escapeHtml(job.source_file)}">
                    <i class="bi bi-film me-2 text-muted"></i>${utils.escapeHtml(fileName)}
                </div>
                <div class="d-flex align-items-center gap-2">
                    <span class="badge ${badgeClass}">${job.status}</span>
                    <button class="btn btn-outline-danger btn-sm cancel-btn" data-job-id="${job.id}" title="${buttonTitle}"><i class="bi ${buttonIcon}"></i></button>
                    ${(job.status !== 'pending') ? `<button class="btn btn-outline-secondary btn-sm log-btn" data-job-id="${job.id}"><i class="bi bi-file-text"></i></button>` : ''}
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

        return element;
    }

    createProgressElement(job) {
        if (job.status === 'pending') {
            return '<div class="text-muted small"><i class="bi bi-hourglass me-1"></i>Waiting in queue...</div>';
        }

        if (job.status === 'processing') {
            const percent = job.progress_percent || 0;
            const fps = job.current_fps ? job.current_fps.toFixed(1) : '0.0';
            const eta = this.formatEta(job.eta_seconds);

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

        if (job.status === 'completed') {
            return '<div class="text-success small"><i class="bi bi-check-all me-1"></i>Conversion complete</div>';
        }

        if (job.status === 'cancelled') {
            return '<div class="text-secondary small"><i class="bi bi-x-circle me-1"></i>Cancelled by user</div>';
        }

        if (job.status === 'failed') {
            const error = job.error_message || 'Unknown error';
            return `<div class="text-danger small"><i class="bi bi-exclamation-triangle me-1"></i>${utils.escapeHtml(error)}</div>`;
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
        
        // Update log modal if open
        if (this.openLogJobId === jobId && progressData.current_log) {
            const logContent = document.getElementById('log-content');
            if (logContent) {
                const scrollContainer = logContent.parentElement;
                // Check if user is near the bottom (within 100px) before update
                const isNearBottom = scrollContainer.scrollHeight - scrollContainer.scrollTop - scrollContainer.clientHeight < 100;

                logContent.textContent = progressData.current_log;

                // Only auto-scroll if user was already near the bottom
                if (isNearBottom) {
                    scrollContainer.scrollTop = scrollContainer.scrollHeight;
                }
            }
        }
    }

    updateJobStatus(jobId, status, error) {
        const job = this.jobs.get(jobId);
        if (!job) {
            this.loadJobs();
            return;
        }

        job.status = status;
        job.error_message = error;

        if (status === 'completed') {
            job.progress_percent = 100;
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

            // Use Bootstrap's Modal API - check if bootstrap is available
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

    formatEta(seconds) {
        if (!seconds || seconds <= 0) return '--:--:--';

        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        const secs = Math.floor(seconds % 60);

        return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
    }

    startRefresh() {
        // Only refresh when WebSocket is disconnected (fallback polling)
        if (!this.refreshInterval) {
            this.refreshInterval = setInterval(() => {
                this.loadJobs();
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
