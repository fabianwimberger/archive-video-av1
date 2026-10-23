class PresetManager {
    constructor() {
        this.presets = [];
        this.editingId = null;
    }

    async init() {
        this.setupEventListeners();
    }

    setupEventListeners() {
        document.getElementById('btn-manage-presets').addEventListener('click', () => {
            window.app.switchView('presets');
        });

        document.getElementById('btn-preset-new').addEventListener('click', () => {
            this.startNew();
        });

        document.getElementById('btn-preset-export-all').addEventListener('click', () => {
            this.exportAll();
        });

        document.getElementById('btn-preset-import').addEventListener('click', () => {
            this.setMode('import');
        });

        document.getElementById('btn-preset-import-cancel').addEventListener('click', () => {
            this.setMode('view');
        });

        document.getElementById('btn-preset-import-confirm').addEventListener('click', () => {
            this.importPresets();
        });

        document.getElementById('btn-preset-remove-all').addEventListener('click', () => {
            this.removeAllPresets();
        });

        document.getElementById('btn-preset-edit-save').addEventListener('click', () => {
            this.saveEdit();
        });

        document.getElementById('btn-preset-edit-cancel').addEventListener('click', () => {
            this.setMode('view');
        });
    }

    async open() {
        await this.loadPresets();
        this.renderList();
        this.setMode('view');
    }

    /* One primary button per view: the mode that is open owns it. */
    setMode(mode) {
        const quiet = (id, isQuiet) => document.getElementById(id).classList.toggle('btn--quiet', isQuiet);
        quiet('btn-preset-new', mode !== 'view');
        quiet('btn-preset-edit-save', mode !== 'edit');
        quiet('btn-preset-import-confirm', mode !== 'import');
        document.getElementById('preset-edit-form').hidden = mode !== 'edit';
        document.getElementById('preset-import-form').hidden = mode !== 'import';
    }

    async loadPresets() {
        try {
            this.presets = await api.listPresets();
        } catch (error) {
            console.error('Error loading presets:', error);
        }
    }

    renderList() {
        const container = document.getElementById('preset-list');
        container.innerHTML = '';

        if (this.presets.length === 0) {
            container.innerHTML = '<p class="empty">No presets found</p>';
            return;
        }

        this.presets.forEach(preset => {
            const item = document.createElement('div');
            item.className = 'preset';

            const badges = [];
            if (preset.is_default) badges.push('<span class="status status--ok">Default</span>');
            if (preset.is_builtin) badges.push('<span class="status status--info">Built-in</span>');

            item.innerHTML = `
                <div class="preset__main">
                    <div class="preset__name"><span>${utils.escapeHtml(preset.name)}</span>${badges.join('')}</div>
                    <div class="preset__meta num">CRF ${preset.crf}, speed ${preset.encoder_preset}, ${preset.max_resolution}p</div>
                </div>
                <div class="preset__actions">
                    <button class="btn btn--quiet btn--small preset-action-edit" type="button" title="Edit preset" ${preset.is_builtin ? 'disabled' : ''}>Edit</button>
                    <button class="btn btn--quiet btn--small preset-action-dup" type="button" title="Duplicate preset">Duplicate</button>
                    <button class="btn btn--quiet btn--small preset-action-del" type="button" title="Delete preset" ${preset.is_builtin ? 'disabled' : ''}>Delete</button>
                    <button class="btn btn--quiet btn--small preset-action-export" type="button" title="Export preset">Export</button>
                    <button class="btn btn--text btn--small preset-action-default" type="button" title="Use as default preset" ${preset.is_default ? 'disabled' : ''}>Set default</button>
                </div>
            `;

            item.querySelector('.preset-action-edit').addEventListener('click', () => this.startEdit(preset.id));
            item.querySelector('.preset-action-dup').addEventListener('click', () => this.duplicatePreset(preset.id));
            item.querySelector('.preset-action-del').addEventListener('click', () => this.deletePreset(preset.id));
            item.querySelector('.preset-action-export').addEventListener('click', () => this.exportPreset(preset.id));
            item.querySelector('.preset-action-default').addEventListener('click', () => this.setDefault(preset.id));

            container.appendChild(item);
        });
    }

    startNew() {
        // Seed from the current settings panel values.
        const current = window.settingsPanel.getCurrentSettings();
        this.editingId = null;
        document.getElementById('preset-edit-title').textContent = 'New preset';
        document.getElementById('preset-edit-id').value = '';
        document.getElementById('preset-edit-name').value = '';
        document.getElementById('preset-edit-description').value = '';
        document.getElementById('preset-edit-crf').value = current.crf;
        document.getElementById('preset-edit-encoder_preset').value = current.encoder_preset;
        svtParamsForm.write(svtParamsForm.presetIds(), current.svt_params);
        document.getElementById('preset-edit-audio_bitrate').value = current.audio_bitrate;
        document.getElementById('preset-edit-skip_crop_detect').checked = current.skip_crop_detect;
        document.getElementById('preset-edit-max_resolution').value = current.max_resolution;
        this.setMode('edit');
        document.getElementById('preset-edit-name').focus();
    }

    startEdit(id) {
        const preset = this.presets.find(p => p.id === id);
        if (!preset) return;
        this.editingId = id;
        document.getElementById('preset-edit-title').textContent = 'Edit preset';
        document.getElementById('preset-edit-id').value = id;
        document.getElementById('preset-edit-name').value = preset.name;
        document.getElementById('preset-edit-description').value = preset.description || '';
        document.getElementById('preset-edit-crf').value = preset.crf;
        document.getElementById('preset-edit-encoder_preset').value = preset.encoder_preset;
        svtParamsForm.write(svtParamsForm.presetIds(), preset.svt_params || '');
        document.getElementById('preset-edit-audio_bitrate').value = preset.audio_bitrate;
        document.getElementById('preset-edit-skip_crop_detect').checked = preset.skip_crop_detect;
        document.getElementById('preset-edit-max_resolution').value = preset.max_resolution;
        this.setMode('edit');
        document.getElementById('preset-edit-name').focus();
    }

    async saveEdit() {
        const payload = {
            name: document.getElementById('preset-edit-name').value.trim(),
            description: document.getElementById('preset-edit-description').value.trim() || null,
            crf: parseInt(document.getElementById('preset-edit-crf').value),
            encoder_preset: parseInt(document.getElementById('preset-edit-encoder_preset').value),
            svt_params: svtParamsForm.read(svtParamsForm.presetIds()),
            audio_bitrate: document.getElementById('preset-edit-audio_bitrate').value.trim(),
            skip_crop_detect: document.getElementById('preset-edit-skip_crop_detect').checked,
            max_resolution: parseInt(document.getElementById('preset-edit-max_resolution').value),
        };

        try {
            if (this.editingId) {
                await api.updatePreset(this.editingId, payload);
                window.app.showNotification('Preset updated', 'success');
            } else {
                await api.createPreset(payload);
                window.app.showNotification('Preset created', 'success');
            }
            await this.loadPresets();
            this.renderList();
            this.setMode('view');
            await window.settingsPanel.loadPresets();
            await window.settingsPanel.populatePresetSelect();
            if (window.historyView) window.historyView.loadPresetsForFilter();
        } catch (error) {
            window.app.showNotification(`Error: ${error.message}`, 'danger');
        }
    }

    async duplicatePreset(id) {
        try {
            await api.duplicatePreset(id);
            window.app.showNotification('Preset duplicated', 'success');
            await this.loadPresets();
            this.renderList();
            await window.settingsPanel.loadPresets();
            await window.settingsPanel.populatePresetSelect();
            if (window.historyView) window.historyView.loadPresetsForFilter();
        } catch (error) {
            window.app.showNotification(`Error: ${error.message}`, 'danger');
        }
    }

    async deletePreset(id) {
        if (!confirm('Delete this preset?')) return;
        try {
            await api.deletePreset(id);
            window.app.showNotification('Preset deleted', 'success');
            await this.loadPresets();
            this.renderList();
            await window.settingsPanel.loadPresets();
            await window.settingsPanel.populatePresetSelect();
            if (window.historyView) window.historyView.loadPresetsForFilter();
        } catch (error) {
            window.app.showNotification(`Error: ${error.message}`, 'danger');
        }
    }

    async removeAllPresets() {
        if (!confirm('Remove all user presets? Built-in presets are kept.')) return;
        try {
            await api.deleteAllPresets();
            window.app.showNotification('All user presets removed', 'success');
            await this.loadPresets();
            this.renderList();
            await window.settingsPanel.loadPresets();
            await window.settingsPanel.populatePresetSelect();
            if (window.historyView) window.historyView.loadPresetsForFilter();
        } catch (error) {
            window.app.showNotification(`Error: ${error.message}`, 'danger');
        }
    }

    async setDefault(id) {
        try {
            await api.setDefaultPreset(id);
            window.app.showNotification('Default preset updated', 'success');
            await this.loadPresets();
            this.renderList();
            await window.settingsPanel.loadPresets();
            await window.settingsPanel.populatePresetSelect();
        } catch (error) {
            window.app.showNotification(`Error: ${error.message}`, 'danger');
        }
    }

    async exportPreset(id) {
        try {
            const data = await api.exportPreset(id);
            this.downloadJson(data, `preset-${id}.json`);
        } catch (error) {
            window.app.showNotification(`Error: ${error.message}`, 'danger');
        }
    }

    async exportAll() {
        try {
            const data = await api.exportAllPresets();
            this.downloadJson(data, 'presets.json');
        } catch (error) {
            window.app.showNotification(`Error: ${error.message}`, 'danger');
        }
    }

    downloadJson(data, filename) {
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        a.click();
        URL.revokeObjectURL(url);
    }

    async importPresets() {
        const fileInput = document.getElementById('preset-import-file');
        const strategy = document.getElementById('preset-import-strategy').value;
        const resultEl = document.getElementById('preset-import-result');

        if (!fileInput.files || fileInput.files.length === 0) {
            resultEl.textContent = 'Choose a file first';
            return;
        }

        try {
            const result = await api.importPresets(fileInput.files[0], strategy);
            resultEl.textContent = `Imported ${result.imported.length}, skipped ${result.skipped.length}, renamed ${result.renamed.length}, errors ${result.errors.length}`;
            await this.loadPresets();
            this.renderList();
            await window.settingsPanel.loadPresets();
            await window.settingsPanel.populatePresetSelect();
            if (window.historyView) window.historyView.loadPresetsForFilter();
            fileInput.value = '';
        } catch (error) {
            resultEl.textContent = `Error: ${error.message}`;
        }
    }
}

const presetManager = new PresetManager();
