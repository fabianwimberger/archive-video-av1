/**
 * File browser component
 *
 * Selection rules:
 *   _conv files       -> NOT selectable, no actions (display only)
 *   Original w/o _conv -> selectable, only convert possible
 *   Original w/ _conv  -> selectable, only delete possible
 *
 * "Select Unconverted" -> selects originals that have NO _conv file
 * "Select Converted"   -> selects originals that already HAVE a _conv file
 */
class FileBrowser {
    constructor() {
        this.selectedFiles = new Set();
        this.currentPath = null;
        this.allFiles = [];
        this.filteredFiles = [];
        this.searchTimeout = null;
    }

    async init() {
        this.setupEventListeners();
        await this.loadFiles();
    }

    setupEventListeners() {
        document.getElementById('btn-refresh').addEventListener('click', () => {
            this.loadFiles(this.currentPath);
        });

        document.getElementById('btn-select-unconverted').addEventListener('click', () => {
            this.selectUnconverted();
        });

        document.getElementById('btn-select-converted').addEventListener('click', () => {
            this.selectConverted();
        });

        document.getElementById('btn-unselect-all').addEventListener('click', () => {
            this.clearSelection();
        });

        document.getElementById('search-files').addEventListener('input', (e) => {
            clearTimeout(this.searchTimeout);
            this.searchTimeout = setTimeout(() => {
                this.filterFiles(e.target.value);
            }, 300);
        });

        document.getElementById('btn-delete-selected').addEventListener('click', async () => {
            const count = this.selectedFiles.size;
            if (count === 0) return;

            if (!confirm(`Are you sure you want to delete ${count} selected file(s)?\nThis cannot be undone.`)) {
                return;
            }

            const btn = document.getElementById('btn-delete-selected');
            const originalText = btn.innerHTML;
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>';

            try {
                const filesToDelete = Array.from(this.selectedFiles);
                let deletedCount = 0;
                let errors = [];

                for (const path of filesToDelete) {
                    try {
                        await api.deleteFile(path);
                        deletedCount++;
                    } catch (error) {
                        console.error(`Error deleting ${path}:`, error);
                        errors.push(`${path.split('/').pop()}: ${error.message}`);
                    }
                }

                if (errors.length > 0) {
                    alert(`Deleted ${deletedCount} files. Errors:\n${errors.join('\n')}`);
                }

                this.clearSelection();
                await this.loadFiles(this.currentPath);

            } catch (error) {
                console.error('Error during batch delete:', error);
                alert('An error occurred while deleting files.');
            } finally {
                btn.innerHTML = originalText;
                this.updateButtonStates();
            }
        });
    }

    async convertSelected() {
        const selected = Array.from(this.selectedFiles);

        // Filter to only convertible files (original files without _conv version)
        const convertibleFiles = this.allFiles.filter(f =>
            selected.includes(f.path) && !this._isConvFile(f) && !f.has_converted
        );

        if (convertibleFiles.length === 0) {
            window.app.showNotification('No convertible files selected', 'danger');
            return;
        }

        const btn = document.getElementById('btn-convert-selected');
        const originalText = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Converting...';

        try {
            const filePaths = convertibleFiles.map(f => f.path);
            const settings = window.settingsPanel.getCurrentSettings();
            const presetId = window.settingsPanel.selectedPresetId;
            const isModified = !document.getElementById('preset-modified-badge').classList.contains('d-none');

            await api.createBatchJobs(filePaths, presetId, isModified ? settings : null);

            await window.jobQueue.loadJobs();
            this.clearSelection();

            window.app.showNotification(`Started ${convertibleFiles.length} conversion(s)`, 'success');

        } catch (error) {
            console.error('Error during batch convert:', error);
            window.app.showNotification(`Failed to create conversion jobs: ${error.message}`, 'danger');
        } finally {
            btn.innerHTML = originalText;
            this.updateButtonStates();
        }
    }

    async loadFiles(path = null) {
        const container = document.getElementById('file-browser');
        container.innerHTML = '<div class="loading">Loading files...</div>';

        try {
            const data = await api.browseFiles(path);
            this.currentPath = data.current_path;
            this.allFiles = data.files;
            this.filteredFiles = [...this.allFiles];
            this.render(data);
        } catch (error) {
            console.error('Error loading files:', error);
            container.innerHTML = '<div class="empty-state">Error loading files</div>';
        }
    }

    filterFiles(query) {
        const lowerQuery = query.toLowerCase();
        this.filteredFiles = this.allFiles.filter(file =>
            file.name.toLowerCase().includes(lowerQuery)
        );
        this.renderFiles();
    }

    render(data) {
        const container = document.getElementById('file-browser');
        container.innerHTML = '';

        // Parent directory button
        if (this.currentPath) {
            const parentButton = document.createElement('div');
            parentButton.className = 'list-group-item list-group-item-action cursor-pointer bg-body-tertiary text-secondary';
            parentButton.innerHTML = `
                <div class="d-flex align-items-center">
                    <i class="bi bi-arrow-return-left me-3 fs-5"></i>
                    <div class="fw-medium">..</div>
                </div>
            `;
            parentButton.addEventListener('click', () => {
                const parts = this.currentPath.split('/');
                parts.pop();
                this.clearSelection();
                this.loadFiles(parts.join('/') || null);
            });
            container.appendChild(parentButton);
        }

        // Directories
        data.directories.forEach(dir => {
            const dirElement = document.createElement('div');
            dirElement.className = 'list-group-item list-group-item-action cursor-pointer';
            dirElement.innerHTML = `
                <div class="d-flex align-items-center">
                    <i class="bi bi-folder-fill me-3 text-warning fs-5"></i>
                    <div class="fw-medium text-truncate">${utils.escapeHtml(dir.name)}</div>
                </div>
            `;
            dirElement.addEventListener('click', () => {
                this.clearSelection();
                this.loadFiles(dir.path);
            });
            container.appendChild(dirElement);
        });

        // Files
        this.renderFiles();
    }

    renderFiles() {
        const container = document.getElementById('file-browser');

        // Remove existing file items
        container.querySelectorAll('.file-item').forEach(el => el.remove());

        // Add filtered files
        this.filteredFiles.forEach(file => {
            const fileElement = document.createElement('div');
            fileElement.className = 'list-group-item list-group-item-action file-item';

            const size = this.formatFileSize(file.size);
            const isConvFile = this._isConvFile(file);

            // _conv files: not selectable, display only
            if (isConvFile) {
                fileElement.innerHTML = `
                    <div class="d-flex align-items-center w-100">
                        <div class="me-3">
                            <span class="ms-2"><i class="bi bi-check-circle-fill text-success me-1"></i></span>
                        </div>
                        <div class="me-3">
                            <i class="bi bi-file-earmark-play text-success fs-5"></i>
                        </div>
                        <div class="flex-grow-1 min-width-0">
                            <div class="fw-medium text-truncate text-success">${utils.escapeHtml(file.name)}</div>
                            <div class="small text-muted">
                                <span>${size}</span>${file.hdr ? ` <span class="badge bg-warning text-dark ms-1">${utils.escapeHtml(file.hdr_format || 'HDR')}</span>` : ''}
                            </div>
                        </div>
                    </div>
                `;
                container.appendChild(fileElement);
                return;
            }

            // Original files: selectable
            const textClass = file.has_converted ? '' : 'text-danger';
            let lastJobHtml = '';
            if (file.last_job) {
                const isSuccess = file.last_job.status === 'completed';
                const icon = isSuccess ? 'bi-check-circle' : 'bi-exclamation-triangle';
                const color = isSuccess ? 'text-success' : 'text-warning';
                const badge = isSuccess
                    ? `${file.last_job.preset_name_snapshot || ''} · ${this.formatSavings(file.last_job.source_size_bytes, file.last_job.output_size_bytes)}`
                    : `${file.last_job.status}`;
                lastJobHtml = `<div class="small ${color}"><i class="bi ${icon} me-1"></i>${utils.escapeHtml(badge)}</div>`;
            }

            if (this.selectedFiles.has(file.path)) {
                fileElement.classList.add('list-group-item-primary');
            }

            fileElement.innerHTML = `
                <div class="d-flex align-items-center w-100">
                    <div class="me-3">
                        <input class="form-check-input" type="checkbox" ${this.selectedFiles.has(file.path) ? 'checked' : ''} data-file-path="${utils.escapeHtml(file.path)}" style="cursor: pointer;">
                    </div>
                    <div class="me-3">
                        <i class="bi bi-file-earmark-play text-primary fs-5"></i>
                    </div>
                    <div class="flex-grow-1 min-width-0">
                        <div class="fw-medium text-truncate ${textClass}">${utils.escapeHtml(file.name)}</div>
                        <div class="small text-muted">
                            <span>${size}</span>${file.hdr ? ` <span class="badge bg-warning text-dark ms-1">${utils.escapeHtml(file.hdr_format || 'HDR')}</span>` : ''}
                        </div>
                        ${lastJobHtml}
                    </div>
                </div>
            `;

            const checkbox = fileElement.querySelector('input[type="checkbox"]');
            checkbox.addEventListener('change', (e) => {
                e.stopPropagation();
                if (checkbox.checked) {
                    this.selectedFiles.add(file.path);
                    fileElement.classList.add('list-group-item-primary');
                } else {
                    this.selectedFiles.delete(file.path);
                    fileElement.classList.remove('list-group-item-primary');
                }
                this.updateSelectionCount();
                this.updateButtonStates();
                if (window.settingsPanel) {
                    window.settingsPanel.updateEstimateButtonState();
                }
            });

            // Click last job badge to jump to history
            const lastJobDiv = fileElement.querySelector('.small.text-success, .small.text-warning');
            if (lastJobDiv && file.last_job) {
                lastJobDiv.style.cursor = 'pointer';
                lastJobDiv.addEventListener('click', (e) => {
                    e.stopPropagation();
                    window.app.switchView('history');
                    if (window.historyView) {
                        window.historyView.filterByFile(file.path);
                    }
                });
            }

            fileElement.addEventListener('click', (e) => {
                if (e.target !== checkbox && e.target !== lastJobDiv) {
                    checkbox.checked = !checkbox.checked;
                    checkbox.dispatchEvent(new Event('change'));
                }
            });

            container.appendChild(fileElement);
        });

        this.updateSelectionCount();
        this.updateButtonStates();
    }

    formatSavings(sourceBytes, outputBytes) {
        if (!sourceBytes || !outputBytes) return '';
        const saved = sourceBytes - outputBytes;
        const percent = Math.round((saved / sourceBytes) * 100);
        if (saved <= 0) return 'no savings';
        return `-${percent}%`;
    }

    /**
     * Select all original files that do NOT have a _conv version yet (for batch convert).
     */
    selectUnconverted() {
        this.filteredFiles.forEach(file => {
            if (!this._isConvFile(file) && !file.has_converted) {
                this.selectedFiles.add(file.path);
            }
        });
        this.renderFiles();
        if (window.settingsPanel) {
            window.settingsPanel.updateEstimateButtonState();
        }
    }

    /**
     * Select all original files that already HAVE a _conv version (for batch delete).
     */
    selectConverted() {
        this.filteredFiles.forEach(file => {
            if (!this._isConvFile(file) && file.has_converted) {
                this.selectedFiles.add(file.path);
            }
        });
        this.renderFiles();
        if (window.settingsPanel) {
            window.settingsPanel.updateEstimateButtonState();
        }
    }

    updateSelectionCount() {
        const count = this.selectedFiles.size;
        document.getElementById('selection-count').textContent =
            `${count} file${count !== 1 ? 's' : ''} selected`;
    }

    updateButtonStates() {
        const hasSelection = this.selectedFiles.size > 0;
        document.getElementById('btn-unselect-all').disabled = !hasSelection;
        document.getElementById('btn-refresh').disabled = false;

        // Only original files can be selected, so check what actions apply
        let allDeletable = hasSelection;
        let allConvertible = hasSelection;
        let convertibleCount = 0;

        if (hasSelection) {
            for (const path of this.selectedFiles) {
                const file = this.allFiles.find(f => f.path === path);
                if (file) {
                    // Original with _conv -> deletable, not convertible
                    // Original without _conv -> convertible, not deletable
                    if (file.has_converted) {
                        allConvertible = false;
                    } else {
                        allDeletable = false;
                        convertibleCount++;
                    }
                }
            }
        }

        // Delete button: enabled only if ALL selected files are deletable (originals with _conv)
        document.getElementById('btn-delete-selected').disabled = !allDeletable;

        // Convert button: enabled only if ALL selected files are convertible (originals without _conv)
        const convertBtn = document.getElementById('btn-convert-selected');
        if (convertBtn) {
            convertBtn.disabled = !allConvertible;
            const convertCountSpan = document.getElementById('convert-count');
            if (convertCountSpan) {
                convertCountSpan.textContent = convertibleCount;
            }
        }
    }

    getSelectedFiles() {
        return Array.from(this.selectedFiles);
    }

    clearSelection() {
        this.selectedFiles.clear();
        this.renderFiles();
        if (window.settingsPanel) {
            window.settingsPanel.updateEstimateButtonState();
        }
    }

    _isConvFile(file) {
        return file.is_converted_file || file.name.endsWith('_conv.mkv');
    }

    formatFileSize(bytes) {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }
}

// Global file browser instance
const fileBrowser = new FileBrowser();
