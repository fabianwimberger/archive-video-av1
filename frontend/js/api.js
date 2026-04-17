/**
 * API client wrapper for backend communication
 */
class ApiClient {
    constructor(baseURL = '/api') {
        this.baseURL = baseURL;
    }

    async request(endpoint, options = {}) {
        const url = `${this.baseURL}${endpoint}`;
        const config = {
            headers: {
                'Content-Type': 'application/json',
                ...options.headers,
            },
            ...options,
        };

        try {
            const response = await fetch(url, config);

            if (!response.ok) {
                let errorMessage;
                try {
                    const errorData = await response.json();
                    errorMessage = errorData.detail || errorData.message || JSON.stringify(errorData);
                } catch {
                    errorMessage = `HTTP ${response.status}: ${response.statusText}`;
                }
                throw new Error(errorMessage);
            }

            return await response.json();
        } catch (error) {
            console.error(`API request failed: ${endpoint}`, error);
            throw error;
        }
    }

    // File endpoints
    async browseFiles(path = null) {
        const params = path ? `?path=${encodeURIComponent(path)}` : '';
        return this.request(`/files${params}`);
    }

    async getFileInfo(path) {
        return this.request(`/files/info?path=${encodeURIComponent(path)}`);
    }

    async deleteConvertedFile(path) {
        return this.request(`/files/converted?path=${encodeURIComponent(path)}`, {
            method: 'DELETE',
        });
    }

    async deleteFile(path) {
        return this.request(`/files?path=${encodeURIComponent(path)}`, {
            method: 'DELETE',
        });
    }

    async analyzeFile(path, suggestPreset = false) {
        const params = new URLSearchParams({ path });
        if (suggestPreset) params.append('suggest_preset', 'true');
        return this.request(`/files/analyze?${params}`);
    }

    // Job endpoints
    async createJob(sourceFile, presetId, settings, notes = null) {
        const body = { source_file: sourceFile };
        if (presetId !== null && presetId !== undefined) body.preset_id = presetId;
        if (settings) body.settings = settings;
        if (notes !== null) body.notes = notes;
        return this.request('/jobs', {
            method: 'POST',
            body: JSON.stringify(body),
        });
    }

    async createBatchJobs(files, presetId, settings, notes = null) {
        const body = { files };
        if (presetId !== null && presetId !== undefined) body.preset_id = presetId;
        if (settings) body.settings = settings;
        if (notes !== null) body.notes = notes;
        return this.request('/jobs/batch', {
            method: 'POST',
            body: JSON.stringify(body),
        });
    }

    async listJobs({ status = null, q = null, presetId = null, dateFrom = null, dateTo = null, sort = 'created_at', order = 'desc', limit = 100, offset = 0 } = {}) {
        const params = new URLSearchParams({ limit, offset, sort, order });
        if (status) params.append('status', status);
        if (q) params.append('q', q);
        if (presetId !== null && presetId !== undefined) params.append('preset_id', presetId);
        if (dateFrom) params.append('date_from', dateFrom);
        if (dateTo) params.append('date_to', dateTo);
        return this.request(`/jobs?${params}`);
    }

    async getJob(jobId) {
        return this.request(`/jobs/${jobId}`);
    }

    async updateJob(jobId, { notes }) {
        return this.request(`/jobs/${jobId}`, {
            method: 'PATCH',
            body: JSON.stringify({ notes }),
        });
    }

    async moveJobPosition(jobId, absolute) {
        return this.request(`/jobs/${jobId}/position`, {
            method: 'PATCH',
            body: JSON.stringify({ absolute }),
        });
    }

    async retryJob(jobId) {
        return this.request(`/jobs/${jobId}/retry`, {
            method: 'POST',
        });
    }

    async saveJobAsPreset(jobId, name, description = null) {
        const params = new URLSearchParams({ name });
        if (description !== null) params.append('description', description);
        return this.request(`/jobs/${jobId}/save-as-preset?${params}`, {
            method: 'POST',
        });
    }

    async deleteOrCancelJob(jobId) {
        return this.request(`/jobs/${jobId}`, {
            method: 'DELETE',
        });
    }

    async clearQueuedJobs() {
        return this.request('/jobs/queued', {
            method: 'DELETE',
        });
    }

    async clearCompletedJobs() {
        return this.request('/jobs/completed', {
            method: 'DELETE',
        });
    }

    async clearAllJobs() {
        return this.request('/jobs/all', {
            method: 'DELETE',
        });
    }

    async deleteHistoryOlderThan(olderThan) {
        return this.request(`/jobs/history?older_than=${encodeURIComponent(olderThan)}`, {
            method: 'DELETE',
        });
    }

    // Preset endpoints
    async listPresets() {
        return this.request('/presets');
    }

    async createPreset(preset) {
        return this.request('/presets', {
            method: 'POST',
            body: JSON.stringify(preset),
        });
    }

    async updatePreset(id, preset) {
        return this.request(`/presets/${id}`, {
            method: 'PATCH',
            body: JSON.stringify(preset),
        });
    }

    async deletePreset(id) {
        return this.request(`/presets/${id}`, {
            method: 'DELETE',
        });
    }

    async duplicatePreset(id) {
        return this.request(`/presets/${id}/duplicate`, {
            method: 'POST',
        });
    }

    async setDefaultPreset(id) {
        return this.request(`/presets/${id}/set-default`, {
            method: 'POST',
        });
    }

    async exportPreset(id) {
        const response = await fetch(`${this.baseURL}/presets/${id}/export`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
    }

    async exportAllPresets() {
        const response = await fetch(`${this.baseURL}/presets/export`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
    }

    async importPresets(file, onConflict) {
        const formData = new FormData();
        formData.append('file', file);
        const response = await fetch(`${this.baseURL}/presets/import?on_conflict=${encodeURIComponent(onConflict)}`, {
            method: 'POST',
            body: formData,
        });
        if (!response.ok) {
            let errorMessage;
            try {
                const errorData = await response.json();
                errorMessage = errorData.detail || errorData.message || JSON.stringify(errorData);
            } catch {
                errorMessage = `HTTP ${response.status}: ${response.statusText}`;
            }
            throw new Error(errorMessage);
        }
        return response.json();
    }

    // Queue endpoints
    async getQueueState() {
        return this.request('/queue');
    }

    async pauseQueue() {
        return this.request('/queue/pause', { method: 'POST' });
    }

    async resumeQueue() {
        return this.request('/queue/resume', { method: 'POST' });
    }

    // System endpoints
    async getHealth() {
        return this.request('/health');
    }
}

// Global API client instance
const api = new ApiClient();

// Shared utility functions
const utils = {
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    },

    formatBytes(bytes) {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
    },

    formatEta(seconds) {
        if (!seconds || seconds <= 0) return '--:--:--';
        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        const secs = Math.floor(seconds % 60);
        return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
    },

    formatDate(isoString) {
        if (!isoString) return '';
        const date = new Date(isoString);
        return date.toLocaleString();
    },

    formatSavings(sourceBytes, outputBytes) {
        if (!sourceBytes || !outputBytes) return '';
        const saved = sourceBytes - outputBytes;
        const percent = Math.round((saved / sourceBytes) * 100);
        if (saved <= 0) return ' <span class="text-muted">(no savings)</span>';
        const savedStr = this.formatBytes(saved);
        return ` <span class="text-success" title="Saved ${savedStr} (${percent}%)">(-${percent}%)</span>`;
    }
};
