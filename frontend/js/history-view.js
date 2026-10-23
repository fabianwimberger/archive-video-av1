class HistoryView {
    constructor() {
        this.jobs = [];
        this.total = 0;
        this.presets = [];
        this.limit = 25;
        this.offset = 0;
        this.detailDialog = null;
        this.clearOlderDialog = null;
    }

    async init() {
        this.detailDialog = document.getElementById('history-detail-modal');
        this.clearOlderDialog = document.getElementById('clear-older-modal');
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
            this.clearOlderDialog.showModal();
        });

        document.getElementById('btn-clear-older-confirm').addEventListener('click', async () => {
            const val = document.getElementById('clear-older-date').value;
            if (!val) return;
            try {
                await api.deleteHistoryOlderThan(new Date(val).toISOString());
                this.offset = 0;
                await this.loadJobs();
                this.clearOlderDialog.close();
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

    statusCell(status) {
        if (status === 'completed') return '<span class="status status--ok">Completed</span>';
        if (status === 'failed') return '<span class="status status--fail">Failed</span>';
        if (status === 'cancelled') return '<span class="status status--warn">Cancelled</span>';
        return `<span class="status status--info">${utils.escapeHtml(status)}</span>`;
    }

    render() {
        const tbody = document.getElementById('history-table-body');
        if (this.jobs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="8"><p class="empty">No history yet</p></td></tr>';
            this.renderPagination();
            return;
        }

        tbody.innerHTML = '';
        this.jobs.forEach(job => {
            const fileName = job.source_file.split('/').pop();
            const preset = job.preset_name_snapshot
                ? utils.escapeHtml(job.preset_name_snapshot)
                : '<span class="muted">Custom</span>';

            const summary = this.encodeSummary(job.settings);
            const savings = utils.formatSavings(job.source_size_bytes, job.output_size_bytes);
            const completed = job.completed_at ? utils.formatDate(job.completed_at) : '';
            const notes = job.notes
                ? utils.escapeHtml(job.notes).substring(0, 40) + (job.notes.length > 40 ? '…' : '')
                : '';

            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td title="${utils.escapeHtml(job.source_file)}">${utils.escapeHtml(fileName)}</td>
                <td class="col-wide">${preset}</td>
                <td class="col-wide"><span class="small muted" title="${utils.escapeHtml(summary)}">${utils.escapeHtml(summary)}</span></td>
                <td>${this.statusCell(job.status)}</td>
                <td class="r num col-savings">${savings}</td>
                <td class="col-wide small muted num">${completed}</td>
                <td class="col-wide small muted">${notes}</td>
                <td>
                    <details class="row-menu">
                        <summary>Actions</summary>
                        <div class="row-menu__list">
                            <button class="history-action-details" type="button" data-id="${job.id}">Encode details</button>
                            <button class="history-action-retry" type="button" data-id="${job.id}">Retry</button>
                            <button class="history-action-preset" type="button" data-id="${job.id}">Save as preset</button>
                            <button class="history-action-use" type="button" data-id="${job.id}">Use these settings</button>
                            <button class="history-action-notes" type="button" data-id="${job.id}">Edit notes</button>
                            <button class="history-action-delete danger" type="button" data-id="${job.id}">Delete</button>
                        </div>
                    </details>
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
        if (settings.encoder_preset != null || settings.preset != null) parts.push(`speed ${settings.encoder_preset ?? settings.preset}`);
        if (settings.svt_params) parts.push(settings.svt_params);
        if (settings.max_resolution) parts.push(`${settings.max_resolution}p`);
        if (settings.audio_bitrate) parts.push(settings.audio_bitrate);
        return parts.join(', ');
    }

    renderPagination() {
        const container = document.getElementById('history-pagination');
        const pages = Math.ceil(this.total / this.limit) || 1;
        const currentPage = Math.floor(this.offset / this.limit) + 1;

        container.innerHTML = `
            <span class="small muted">Showing ${this.jobs.length} of ${this.total} jobs</span>
            <div class="cluster">
                <button class="btn btn--quiet btn--small" id="hist-prev" type="button" ${this.offset === 0 ? 'disabled' : ''}>Previous</button>
                <span class="small muted num">Page ${currentPage} / ${pages}</span>
                <button class="btn btn--quiet btn--small" id="hist-next" type="button" ${this.offset + this.limit >= this.total ? 'disabled' : ''}>Next</button>
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
            <span>Total <b class="num">${this.total}</b></span>
            <span>Saved <b class="num">${totalSaved > 0 ? utils.formatBytes(totalSaved) : '—'}</b></span>
            <span>Average saving <b class="num">${avgSavings > 0 ? avgSavings + '%' : '—'}</b></span>
            <span>Presets ${presetParts.join(', ') || '—'}</span>
        `;
    }

    async showDetails(jobId) {
        try {
            const job = await api.getJob(jobId);
            const content = document.getElementById('history-detail-content');
            const settingsRows = Object.entries(job.settings || {})
                .map(([k, v]) => `<dt>${utils.escapeHtml(k)}</dt><dd>${utils.escapeHtml(String(v))}</dd>`)
                .join('');

            content.innerHTML = `
                <dl class="detail-list">
                    <dt>File</dt><dd>${utils.escapeHtml(job.source_file)}</dd>
                    <dt>Output</dt><dd>${utils.escapeHtml(job.output_file)}</dd>
                    <dt>Status</dt><dd>${this.statusCell(job.status)}</dd>
                    <dt>Created</dt><dd class="num">${utils.formatDate(job.created_at)}</dd>
                    <dt>Started</dt><dd class="num">${utils.formatDate(job.started_at)}</dd>
                    <dt>Completed</dt><dd class="num">${utils.formatDate(job.completed_at)}</dd>
                    <dt>Error</dt><dd>${utils.escapeHtml(job.error_message || '—')}</dd>
                    <dt>Notes</dt><dd>${utils.escapeHtml(job.notes || '—')}</dd>
                </dl>
                <div class="detail-block">
                    <h3 class="label">Settings</h3>
                    <dl class="detail-list machine">${settingsRows || '<dt>—</dt><dd></dd>'}</dl>
                </div>
                <div class="detail-block">
                    <h3 class="label">Log</h3>
                    <pre class="log">${utils.escapeHtml(job.log || 'No log')}</pre>
                </div>
            `;
            this.detailDialog.showModal();
        } catch (error) {
            window.app.showNotification(`Error: ${error.message}`, 'danger');
        }
    }

    async retryJob(jobId) {
        try {
            await api.retryJob(jobId);
            window.app.showNotification('Retry started. View it in the queue.', 'success');
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
        svtParamsForm.write(svtParamsForm.mainIds(), settings.svt_params || '');
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
