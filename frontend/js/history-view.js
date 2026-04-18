/**
 * History view component
 */
class HistoryView {
    constructor() {
        this.jobs = [];
        this.total = 0;
        this.presets = [];
        this.limit = 25;
        this.offset = 0;
        this.detailModal = null;
        this.clearOlderModal = null;
    }

    async init() {
        this.detailModal = bootstrap.Modal.getOrCreateInstance(document.getElementById('history-detail-modal'));
        this.clearOlderModal = bootstrap.Modal.getOrCreateInstance(document.getElementById('clear-older-modal'));
        this.setupEventListeners();
        await this.loadPresetsForFilter();
        await this.loadJobs();
    }

    setupEventListeners() {
        document.getElementById('btn-history-apply').addEventListener('click', () => {
            this.offset = 0;
            this.loadJobs();
        });

        document.getElementById('btn-history-clear-finished').addEventListener('click', async () => {
            if (!confirm('Clear all finished jobs?')) return;
            try {
                await api.clearCompletedJobs();
                this.offset = 0;
                await this.loadJobs();
                window.app.showNotification('History cleared', 'success');
            } catch (error) {
                window.app.showNotification(`Error: ${error.message}`, 'danger');
            }
        });

        document.getElementById('btn-history-clear-older').addEventListener('click', () => {
            this.clearOlderModal.show();
        });

        document.getElementById('btn-clear-older-confirm').addEventListener('click', async () => {
            const val = document.getElementById('clear-older-date').value;
            if (!val) return;
            try {
                await api.deleteHistoryOlderThan(new Date(val).toISOString());
                this.offset = 0;
                await this.loadJobs();
                this.clearOlderModal.hide();
                window.app.showNotification('Old history cleared', 'success');
            } catch (error) {
                window.app.showNotification(`Error: ${error.message}`, 'danger');
            }
        });

        document.getElementById('btn-history-export-csv').addEventListener('click', () => {
            this.exportCsv();
        });
    }

    async loadPresetsForFilter() {
        try {
            this.presets = await api.listPresets();
            const select = document.getElementById('history-filter-preset');
            select.innerHTML = '<option value="" selected>All presets</option>';
            this.presets.forEach(p => {
                const opt = document.createElement('option');
                opt.value = p.id;
                opt.textContent = p.name;
                select.appendChild(opt);
            });
        } catch (e) {
            console.error('Error loading presets for filter:', e);
        }
    }

    async loadJobs() {
        const statusSelect = document.getElementById('history-filter-status');
        const statuses = Array.from(statusSelect.selectedOptions).map(o => o.value).filter(v => v);
        const q = document.getElementById('history-search').value.trim() || null;
        const presetId = document.getElementById('history-filter-preset').value || null;
        const dateFrom = document.getElementById('history-date-from').value || null;
        const dateTo = document.getElementById('history-date-to').value || null;

        try {
            const data = await api.listJobs({
                status: statuses.length ? statuses.join(',') : 'completed,failed,cancelled',
                q,
                presetId,
                dateFrom,
                dateTo,
                sort: 'completed_at',
                order: 'desc',
                limit: this.limit,
                offset: this.offset,
            });
            this.jobs = data.jobs;
            this.total = data.total;
            this.render();
            this.renderStats();
        } catch (error) {
            console.error('Error loading history:', error);
        }
    }

    filterByFile(filePath) {
        document.getElementById('history-search').value = filePath;
        this.offset = 0;
        this.loadJobs();
    }

    render() {
        const tbody = document.getElementById('history-table-body');
        if (this.jobs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted py-4">No history yet</td></tr>';
            this.renderPagination();
            return;
        }

        tbody.innerHTML = '';
        this.jobs.forEach(job => {
            const fileName = job.source_file.split('/').pop();
            const presetBadge = job.preset_name_snapshot
                ? `<span class="badge bg-info text-dark">${utils.escapeHtml(job.preset_name_snapshot)}</span>`
                : '<span class="badge bg-secondary">Custom</span>';

            let statusBadge = '<span class="badge bg-secondary">' + job.status + '</span>';
            if (job.status === 'completed') statusBadge = '<span class="badge bg-success">completed</span>';
            if (job.status === 'failed') statusBadge = '<span class="badge bg-danger">failed</span>';
            if (job.status === 'cancelled') statusBadge = '<span class="badge bg-warning text-dark">cancelled</span>';

            const summary = this.encodeSummary(job.settings);
            const savings = utils.formatSavings(job.source_size_bytes, job.output_size_bytes);
            const completed = job.completed_at ? utils.formatDate(job.completed_at) : '';
            const notes = job.notes ? utils.escapeHtml(job.notes).substring(0, 40) + (job.notes.length > 40 ? '…' : '') : '';

            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td title="${utils.escapeHtml(job.source_file)}">${utils.escapeHtml(fileName)}</td>
                <td>${presetBadge}</td>
                <td><div class="small text-muted" title="${utils.escapeHtml(summary)}">${utils.escapeHtml(summary)}</div></td>
                <td>${statusBadge}</td>
                <td>${savings}</td>
                <td class="small text-muted">${completed}</td>
                <td class="small text-muted">${notes}</td>
                <td class="text-end">
                    <div class="dropdown">
                        <button class="btn btn-sm btn-outline-secondary dropdown-toggle" type="button" data-bs-toggle="dropdown">Actions</button>
                        <ul class="dropdown-menu dropdown-menu-end">
                            <li><button class="dropdown-item history-action-details" data-id="${job.id}">Encode details</button></li>
                            <li><button class="dropdown-item history-action-retry" data-id="${job.id}">Retry</button></li>
                            <li><button class="dropdown-item history-action-preset" data-id="${job.id}">Save as preset</button></li>
                            <li><button class="dropdown-item history-action-use" data-id="${job.id}">Use these settings</button></li>
                            <li><button class="dropdown-item history-action-notes" data-id="${job.id}">Edit notes</button></li>
                            <li><hr class="dropdown-divider"></li>
                            <li><button class="dropdown-item text-danger history-action-delete" data-id="${job.id}">Delete</button></li>
                        </ul>
                    </div>
                </td>
            `;

            tr.querySelector('.history-action-details').addEventListener('click', () => this.showDetails(job.id));
            tr.querySelector('.history-action-retry').addEventListener('click', () => this.retryJob(job.id));
            tr.querySelector('.history-action-preset').addEventListener('click', () => this.saveAsPreset(job.id));
            tr.querySelector('.history-action-use').addEventListener('click', () => this.useSettings(job));
            tr.querySelector('.history-action-notes').addEventListener('click', () => this.editNotes(job.id));
            tr.querySelector('.history-action-delete').addEventListener('click', () => this.deleteJob(job.id));

            tbody.appendChild(tr);
        });

        this.renderPagination();
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

    renderPagination() {
        const container = document.getElementById('history-pagination');
        const pages = Math.ceil(this.total / this.limit) || 1;
        const currentPage = Math.floor(this.offset / this.limit) + 1;

        container.innerHTML = `
            <div class="small text-muted">Showing ${this.jobs.length} of ${this.total} jobs</div>
            <div class="btn-group btn-group-sm">
                <button class="btn btn-outline-secondary" id="hist-prev" ${this.offset === 0 ? 'disabled' : ''}>Previous</button>
                <button class="btn btn-outline-secondary" disabled>Page ${currentPage} / ${pages}</button>
                <button class="btn btn-outline-secondary" id="hist-next" ${this.offset + this.limit >= this.total ? 'disabled' : ''}>Next</button>
            </div>
        `;

        const prevBtn = document.getElementById('hist-prev');
        const nextBtn = document.getElementById('hist-next');
        if (prevBtn) prevBtn.addEventListener('click', () => { this.offset = Math.max(0, this.offset - this.limit); this.loadJobs(); });
        if (nextBtn) nextBtn.addEventListener('click', () => { this.offset += this.limit; this.loadJobs(); });
    }

    renderStats() {
        const completed = this.jobs.filter(j => j.status === 'completed' && j.source_size_bytes && j.output_size_bytes);
        const totalSaved = completed.reduce((sum, j) => sum + (j.source_size_bytes - j.output_size_bytes), 0);
        const avgSavings = completed.length
            ? Math.round(completed.reduce((sum, j) => sum + ((j.source_size_bytes - j.output_size_bytes) / j.source_size_bytes * 100), 0) / completed.length)
            : 0;

        const presetCounts = {};
        this.jobs.forEach(j => {
            const name = j.preset_name_snapshot || 'Custom';
            presetCounts[name] = (presetCounts[name] || 0) + 1;
        });
        const presetParts = Object.entries(presetCounts).map(([name, count]) => `${name}: ${count}`);

        const el = document.getElementById('history-stats');
        el.innerHTML = `
            <span><strong>Total:</strong> ${this.total}</span>
            <span><strong>Saved:</strong> ${totalSaved > 0 ? utils.formatBytes(totalSaved) : '—'}</span>
            <span><strong>Avg savings:</strong> ${avgSavings > 0 ? avgSavings + '%' : '—'}</span>
            <span><strong>Presets:</strong> ${presetParts.join(' · ') || '—'}</span>
        `;
    }

    async showDetails(jobId) {
        try {
            const job = await api.getJob(jobId);
            const content = document.getElementById('history-detail-content');
            const settingsHtml = Object.entries(job.settings || {}).map(([k, v]) => `<div><code>${utils.escapeHtml(k)}</code>: ${utils.escapeHtml(String(v))}</div>`).join('');
            content.innerHTML = `
                <dl class="row">
                    <dt class="col-sm-4">File</dt><dd class="col-sm-8 text-break">${utils.escapeHtml(job.source_file)}</dd>
                    <dt class="col-sm-4">Output</dt><dd class="col-sm-8 text-break">${utils.escapeHtml(job.output_file)}</dd>
                    <dt class="col-sm-4">Status</dt><dd class="col-sm-8">${job.status}</dd>
                    <dt class="col-sm-4">Created</dt><dd class="col-sm-8">${utils.formatDate(job.created_at)}</dd>
                    <dt class="col-sm-4">Started</dt><dd class="col-sm-8">${utils.formatDate(job.started_at)}</dd>
                    <dt class="col-sm-4">Completed</dt><dd class="col-sm-8">${utils.formatDate(job.completed_at)}</dd>
                    <dt class="col-sm-4">Error</dt><dd class="col-sm-8 text-danger">${utils.escapeHtml(job.error_message || '—')}</dd>
                    <dt class="col-sm-4">Notes</dt><dd class="col-sm-8">${utils.escapeHtml(job.notes || '—')}</dd>
                </dl>
                <h6>Settings</h6>
                <div class="bg-body-tertiary p-2 rounded small mb-3">${settingsHtml}</div>
                <h6>Log</h6>
                <pre class="bg-dark text-light p-2 rounded small" style="max-height: 300px; overflow: auto;">${utils.escapeHtml(job.log || 'No log')}</pre>
            `;
            this.detailModal.show();
        } catch (error) {
            window.app.showNotification(`Error: ${error.message}`, 'danger');
        }
    }

    async retryJob(jobId) {
        try {
            const result = await api.retryJob(jobId);
            window.app.showNotification(`Retry started. <a href="#/convert" class="alert-link" onclick="app.switchView('convert')">View in queue</a>`, 'success');
            window.jobQueue.loadJobs();
        } catch (error) {
            window.app.showNotification(`Error: ${error.message}`, 'danger');
        }
    }

    async saveAsPreset(jobId) {
        const name = prompt('Preset name:');
        if (!name) return;
        try {
            await api.saveJobAsPreset(jobId, name);
            window.app.showNotification('Preset saved', 'success');
            await window.settingsPanel.loadPresets();
            await window.settingsPanel.populatePresetSelect();
            await this.loadPresetsForFilter();
        } catch (error) {
            window.app.showNotification(`Error: ${error.message}`, 'danger');
        }
    }

    useSettings(job) {
        window.app.switchView('convert');
        const settings = job.settings || {};
        document.getElementById('crf-slider').value = settings.crf ?? 26;
        document.getElementById('crf-value').textContent = settings.crf ?? 26;
        document.getElementById('preset-slider').value = settings.encoder_preset ?? settings.preset ?? 4;
        document.getElementById('preset-value').textContent = settings.encoder_preset ?? settings.preset ?? 4;
        document.getElementById('svt-params').value = settings.svt_params || '';
        document.getElementById('audio-bitrate').value = settings.audio_bitrate || '96k';
        document.getElementById('skip-crop').checked = settings.skip_crop_detect || false;
        const res = settings.max_resolution || 1080;
        const radio = document.querySelector(`input[name="resolution"][value="${res}"]`);
        if (radio) radio.checked = true;
        window.settingsPanel.selectedPresetId = null;
        document.getElementById('preset-select').value = '';
        window.settingsPanel.checkModified();
        window.app.showNotification('Settings applied from history job', 'success');
    }

    async editNotes(jobId) {
        try {
            const job = await api.getJob(jobId);
            const newNotes = prompt('Notes:', job.notes || '');
            if (newNotes === null) return;
            await api.updateJob(jobId, { notes: newNotes });
            await this.loadJobs();
        } catch (error) {
            window.app.showNotification(`Error: ${error.message}`, 'danger');
        }
    }

    async deleteJob(jobId) {
        if (!confirm('Delete this history entry?')) return;
        try {
            await api.deleteOrCancelJob(jobId);
            await this.loadJobs();
        } catch (error) {
            window.app.showNotification(`Error: ${error.message}`, 'danger');
        }
    }

    exportCsv() {
        const headers = ['ID', 'File', 'Preset', 'Status', 'Source Size', 'Output Size', 'Completed', 'Notes'];
        const rows = this.jobs.map(j => [
            j.id,
            j.source_file,
            j.preset_name_snapshot || 'Custom',
            j.status,
            j.source_size_bytes || '',
            j.output_size_bytes || '',
            j.completed_at || '',
            (j.notes || '').replace(/"/g, '""'),
        ]);
        const csv = [headers, ...rows]
            .map(r => r.map(c => `"${String(c).replace(/"/g, '""')}"`).join(','))
            .join('\n');
        const blob = new Blob([csv], { type: 'text/csv' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'history.csv';
        a.click();
        URL.revokeObjectURL(url);
    }
}

const historyView = new HistoryView();
