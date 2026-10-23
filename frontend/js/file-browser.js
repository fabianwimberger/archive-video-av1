/**
 * File browser component
 *
 * Selection rules:
 *   _conv files        -> NOT selectable, display only
 *   Original w/o _conv -> selectable, only convert possible
 *   Original w/ _conv  -> selectable, only delete possible
 *
 * "Select unconverted" -> selects originals that have NO _conv file
 * "Select converted"   -> selects originals that already HAVE a _conv file
 */
class FileBrowser {
    constructor() {
        this.selectedFiles = new Set();
        this.currentPath = null;
        this.allFiles = [];
        this.filteredFiles = [];
        this.directories = [];
        this.filterMode = 'all';
        this.searchTimeout = null;
        this.narrowQuery = window.matchMedia('(max-width: 63.99rem)');
    }

    async init() {
        this.setupEventListeners();
        this.narrowQuery.addEventListener('change', () => {
            if (this.allFiles.length || this.directories.length) this.render();
        });
        await this.loadFiles();
    }

    setupEventListeners() {
        document.getElementById('btn-refresh').addEventListener('click', () => {
            this.loadFiles(this.currentPath || null);
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

        document.getElementById('search-files').addEventListener('input', () => {
            clearTimeout(this.searchTimeout);
            this.searchTimeout = setTimeout(() => this.applyFilters(), 300);
        });

        document.querySelectorAll('[data-file-filter]').forEach(button => {
            button.addEventListener('click', () => {
                this.filterMode = button.dataset.fileFilter;
                document.querySelectorAll('[data-file-filter]').forEach(other => {
                    other.setAttribute('aria-pressed', String(other === button));
                });
                this.applyFilters();
            });
        });

        document.getElementById('btn-delete-selected').addEventListener('click', async () => {
            const count = this.selectedFiles.size;
            if (count === 0) return;

            if (!confirm(`Delete ${count} selected file${count === 1 ? '' : 's'}?\nThis cannot be undone.`)) {
                return;
            }

            const btn = document.getElementById('btn-delete-selected');
            const originalText = btn.textContent;
            btn.disabled = true;
            btn.textContent = 'Deleting…';

            try {
                const filesToDelete = Array.from(this.selectedFiles);
                let deletedCount = 0;
                const errors = [];

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
                await this.loadFiles(this.currentPath || null);
                window.app.showNotification(`${deletedCount} file${deletedCount === 1 ? '' : 's'} deleted`, 'success');
            } catch (error) {
                console.error('Error during batch delete:', error);
                alert('An error occurred while deleting files.');
            } finally {
                btn.textContent = originalText;
                this.updateButtonStates();
            }
        });
    }

    async convertSelected() {
        const selected = Array.from(this.selectedFiles);

        // Only originals without a _conv version can be converted.
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
        btn.textContent = 'Converting…';

        try {
            const filePaths = convertibleFiles.map(f => f.path);
            const settings = window.settingsPanel.getCurrentSettings();
            const presetId = window.settingsPanel.selectedPresetId;
            const isModified = !document.getElementById('preset-modified-badge').hidden;

            await api.createBatchJobs(filePaths, presetId, isModified ? settings : null);

            await window.jobQueue.loadJobs();
            this.clearSelection();

            window.app.showNotification(`Started ${convertibleFiles.length} conversion${convertibleFiles.length === 1 ? '' : 's'}`, 'success');
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
        container.innerHTML = '<p class="empty">Loading files…</p>';

        try {
            const data = await api.browseFiles(path);
            this.currentPath = data.current_path;
            this.allFiles = data.files;
            this.directories = data.directories;
            this.filteredFiles = [...this.allFiles];

            const context = document.getElementById('browse-path');
            if (this.currentPath) {
                context.textContent = `/${this.currentPath}`;
                context.hidden = false;
            } else {
                context.textContent = '';
                context.hidden = true;
            }

            this.applyFilters();
        } catch (error) {
            console.error('Error loading files:', error);
            container.innerHTML = '<p class="empty">Could not load this folder.</p>';
        }
    }

    applyFilters() {
        const query = document.getElementById('search-files').value.trim().toLowerCase();

        this.filteredFiles = this.allFiles.filter(file => {
            if (query && !file.name.toLowerCase().includes(query)) return false;
            if (this.filterMode === 'unconverted') {
                return !this._isConvFile(file) && !file.has_converted;
            }
            if (this.filterMode === 'converted') {
                return this._isConvFile(file) || file.has_converted;
            }
            return true;
        });

        this.render();
    }

    render() {
        const container = document.getElementById('file-browser');
        const narrow = this.narrowQuery.matches;
        const columns = narrow ? 2 : 4;

        const table = document.createElement('table');
        table.className = 'data-table files-table';

        const head = document.createElement('thead');
        head.innerHTML = `
            <tr>
                <th class="col-check"><input type="checkbox" id="select-all-files" aria-label="Select all files shown that can be converted" title="Select all files shown that can be converted"></th>
                <th>File</th>
                ${narrow ? '' : '<th class="col-status">Status</th><th class="col-last">Last conversion</th>'}
            </tr>
        `;
        table.appendChild(head);

        const tbody = document.createElement('tbody');
        table.appendChild(tbody);
        container.replaceChildren(table);

        document.getElementById('select-all-files').addEventListener('change', (event) => {
            this.toggleAllShown(event.target.checked);
        });

        if (this.currentPath) {
            tbody.appendChild(this.createParentRow(columns));
        }

        this.directories.forEach(directory => {
            tbody.appendChild(this.createDirectoryRow(directory, columns));
        });

        this.filteredFiles.forEach(file => {
            tbody.appendChild(this.createFileRow(file, narrow));
        });

        if (this.filteredFiles.length === 0) {
            const filtering = this.filterMode !== 'all'
                || document.getElementById('search-files').value.trim() !== '';
            tbody.appendChild(this.createEmptyRow(
                filtering ? 'No files match the search' : 'No videos in this folder',
                columns,
            ));
        }

        this.updateSelectionCount();
        this.updateButtonStates();
    }

    createEmptyRow(message, columns) {
        const row = document.createElement('tr');
        row.innerHTML = `<td colspan="${columns}"><p class="empty">${utils.escapeHtml(message)}</p></td>`;
        return row;
    }

    createParentRow(columns) {
        const row = document.createElement('tr');
        row.className = 'row-dir';
        row.innerHTML = `
            <td colspan="${columns}">
                <button class="row-dir__button" type="button">
                    <svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7"/></svg>
                    Parent folder
                </button>
            </td>
        `;
        row.querySelector('button').addEventListener('click', () => {
            const parts = this.currentPath.split('/');
            parts.pop();
            this.clearSelection();
            this.loadFiles(parts.join('/') || null);
        });
        return row;
    }

    createDirectoryRow(directory, columns) {
        const row = document.createElement('tr');
        row.className = 'row-dir';
        row.innerHTML = `
            <td colspan="${columns}">
                <button class="row-dir__button" type="button">
                    <svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>
                    ${utils.escapeHtml(directory.name)}
                </button>
            </td>
        `;
        row.querySelector('button').addEventListener('click', () => {
            this.clearSelection();
            this.loadFiles(directory.path);
        });
        return row;
    }

    createFileRow(file, narrow) {
        const row = document.createElement('tr');
        row.className = 'file-row';
        row.dataset.path = file.path;
        if (this.selectedFiles.has(file.path)) {
            row.setAttribute('aria-selected', 'true');
        }

        const isConvFile = this._isConvFile(file);
        if (isConvFile) row.classList.add('file-row--static');
        const size = this.formatFileSize(file.size);
        const modified = file.mtime ? utils.formatDate(new Date(file.mtime * 1000).toISOString(), { dateOnly: true }) : '';

        let status;
        if (isConvFile) {
            status = '<span class="status status--ok">Converted file</span>';
        } else if (file.has_converted) {
            status = '<span class="status status--ok">Converted</span>';
        } else {
            status = '<span class="status status--warn">Not converted</span>';
        }

        const meta = [`<span class="num">${size}</span>`];
        if (modified) meta.push(`<span class="num">${modified}</span>`);
        if (file.hdr) {
            meta.push(`<span class="status status--info">${utils.escapeHtml(file.hdr_format || 'HDR')}</span>`);
        }
        if (narrow) {
            meta.push(`<span class="file-row__status-inline">${status}</span>`);
            meta.push('<span class="file-row__last-cell"></span>');
        }

        row.innerHTML = `
            <td class="col-check"></td>
            <td>
                <span class="file-row__name">
                    <span class="file-row__title" title="${utils.escapeHtml(file.path)}">${utils.escapeHtml(file.name)}</span>
                    <span class="file-row__meta">${meta.join('')}</span>
                </span>
            </td>
            ${narrow ? '' : `<td class="col-status">${status}</td><td class="file-row__last-cell"></td>`}
        `;

        const lastCell = row.querySelector('.file-row__last-cell');

        if (!isConvFile) {
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.checked = this.selectedFiles.has(file.path);
            checkbox.setAttribute('aria-label', `Select ${file.name}`);
            checkbox.addEventListener('change', () => {
                if (checkbox.checked) {
                    this.selectedFiles.add(file.path);
                } else {
                    this.selectedFiles.delete(file.path);
                }
                row.setAttribute('aria-selected', String(checkbox.checked));
                this.updateSelectionCount();
                this.updateButtonStates();
                if (window.settingsPanel) {
                    window.settingsPanel.updateEstimateButtonState();
                }
            });
            row.querySelector('.col-check').appendChild(checkbox);

            row.addEventListener('click', (event) => {
                if (event.target.closest('button, input')) return;
                checkbox.checked = !checkbox.checked;
                checkbox.dispatchEvent(new Event('change'));
            });
        }

        if (file.last_job) {
            const label = this.formatLastJob(file.last_job);
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'file-row__last';
            button.textContent = label;
            button.title = 'Show this file in history';
            button.addEventListener('click', (event) => {
                event.stopPropagation();
                window.app.switchView('history');
                if (window.historyView) {
                    window.historyView.filterByFile(file.path);
                }
            });
            lastCell.appendChild(button);
        } else if (!narrow) {
            lastCell.innerHTML = '<span class="muted">—</span>';
        }

        return row;
    }

    formatLastJob(job) {
        if (job.status === 'completed') {
            const parts = [];
            if (job.preset_name_snapshot) parts.push(job.preset_name_snapshot);
            const savings = this.formatSavings(job.source_size_bytes, job.output_size_bytes);
            if (savings) parts.push(savings);
            return parts.join(', ') || 'Completed';
        }
        return job.status;
    }

    formatSavings(sourceBytes, outputBytes) {
        if (!sourceBytes || !outputBytes) return '';
        const saved = sourceBytes - outputBytes;
        if (saved <= 0) return 'no savings';
        return `−${Math.round((saved / sourceBytes) * 100)}%`;
    }

    toggleAllShown(checked) {
        this.filteredFiles.forEach(file => {
            if (this._isConvFile(file) || file.has_converted) return;
            if (checked) {
                this.selectedFiles.add(file.path);
            } else {
                this.selectedFiles.delete(file.path);
            }
        });
        this.render();
        if (window.settingsPanel) {
            window.settingsPanel.updateEstimateButtonState();
        }
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
        this.render();
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
        this.render();
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

        // Only original files can be selected, so check what actions apply.
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

        document.getElementById('btn-delete-selected').disabled = !allDeletable;

        const convertBtn = document.getElementById('btn-convert-selected');
        if (convertBtn) {
            convertBtn.disabled = !allConvertible;
            const convertCountSpan = document.getElementById('convert-count');
            if (convertCountSpan) {
                convertCountSpan.textContent = convertibleCount;
            }
            const convertUnit = document.getElementById('convert-unit');
            if (convertUnit) {
                convertUnit.textContent = convertibleCount === 1 ? 'file' : 'files';
            }
        }

        this.updateSelectAllState();
    }

    updateSelectAllState() {
        const selectAll = document.getElementById('select-all-files');
        if (!selectAll) return;

        const shown = this.filteredFiles.filter(file => !this._isConvFile(file) && !file.has_converted);
        const selected = shown.filter(file => this.selectedFiles.has(file.path)).length;

        selectAll.checked = shown.length > 0 && selected === shown.length;
        selectAll.indeterminate = selected > 0 && selected < shown.length;
        selectAll.disabled = shown.length === 0;
    }

    getSelectedFiles() {
        return Array.from(this.selectedFiles);
    }

    clearSelection() {
        this.selectedFiles.clear();
        this.render();
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

const fileBrowser = new FileBrowser();
