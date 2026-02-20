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

    // Job endpoints
    async createJob(sourceFile, mode, settings) {
        return this.request('/jobs', {
            method: 'POST',
            body: JSON.stringify({
                source_file: sourceFile,
                mode,
                settings,
            }),
        });
    }

    async createBatchJobs(files, mode, settings) {
        return this.request('/jobs/batch', {
            method: 'POST',
            body: JSON.stringify({
                files,
                mode,
                settings,
            }),
        });
    }

    async listJobs(status = null, limit = 100, offset = 0) {
        const params = new URLSearchParams({ limit, offset });
        if (status) params.append('status', status);
        return this.request(`/jobs?${params}`);
    }

    async getJob(jobId) {
        return this.request(`/jobs/${jobId}`);
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

    // System endpoints
    async getHealth() {
        return this.request('/health');
    }

    async getPresets() {
        return this.request('/presets');
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
    }
};
