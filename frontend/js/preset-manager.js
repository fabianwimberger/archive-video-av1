/**
 * Preset manager modal component
 */
class PresetManager {
    constructor() {
        this.presets = [];
        this.modal = null;
        this.editingId = null;
    }

    async init() {
        this.modal = bootstrap.Modal.getOrCreateInstance(document.getElementById('preset-modal'));
        this.setupEventListeners();
    }

    setupEventListeners() {
        document.getElementById('btn-manage-presets').addEventListener('click', () => {
            this.open();
        });

        document.getElementById('btn-preset-new').addEventListener('click', () => {
            this.startNew();
        });

        document.getElementById('btn-preset-export-all').addEventListener('click', () => {
            this.exportAll();
        });

        document.getElementById('btn-preset-import').addEventListener('click', () => {
            document.getElementById('preset-import-form').classList.remove('d-none');
            document.getElementById('preset-edit-form').classList.add('d-none');
        });

        document.getElementById('btn-preset-import-cancel').addEventListener('click', () => {
            document.getElementById('preset-import-form').classList.add('d-none');
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
            document.getElementById('preset-edit-form').classList.add('d-none');
        });
    }

    async open() {
        await this.loadPresets();
        this.renderList();
        document.getElementById('preset-edit-form').classList.add('d-none');
        document.getElementById('preset-import-form').classList.add('d-none');
        this.modal.show();
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
            container.innerHTML = '<div class="list-group-item text-muted">No presets found</div>';
            return;
        }

        this.presets.forEach(preset => {
            const item = document.createElement('div');
            item.className = 'list-group-item d-flex justify-content-between align-items-center';

            const isDefaultBadge = preset.is_default ? '<span class="badge bg-success ms-2">Default</span>' : '';
            const builtinBadge = preset.is_builtin ? '<span class="badge bg-secondary ms-2">Built-in</span>' : '';

            item.innerHTML = `
                <div>
                    <span class="fw-medium">${utils.escapeHtml(preset.name)}</span>
                    ${isDefaultBadge}
                    ${builtinBadge}
                    <div class="small text-muted">CRF ${preset.crf} · preset ${preset.encoder_preset} · ${preset.max_resolution}p</div>
                </div>
                <div class="d-flex gap-1">
                    <button class="btn btn-sm btn-outline-primary preset-action-edit" data-id="${preset.id}" title="Edit" ${preset.is_builtin ? 'disabled' : ''}>Edit</button>
                    <button class="btn btn-sm btn-outline-secondary preset-action-dup" data-id="${preset.id}" title="Duplicate">Dup</button>
                    <button class="btn btn-sm btn-outline-danger preset-action-del" data-id="${preset.id}" title="Delete" ${preset.is_builtin ? 'disabled' : ''}>Del</button>
                    <button class="btn btn-sm btn-outline-secondary preset-action-export" data-id="${preset.id}" title="Export">Exp</button>
                    <button class="btn btn-sm btn-outline-success preset-action-default" data-id="${preset.id}" title="Set as default" ${preset.is_default ? 'disabled' : ''}>Def</button>
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
        // Seed from current form values
        const current = window.settingsPanel.getCurrentSettings();
        this.editingId = null;
        document.getElementById('preset-edit-id').value = '';
        document.getElementById('preset-edit-name').value = '';
        document.getElementById('preset-edit-description').value = '';
        document.getElementById('preset-edit-crf').value = current.crf;
        document.getElementById('preset-edit-encoder_preset').value = current.encoder_preset;
        document.getElementById('preset-edit-svt_params').value = current.svt_params;
        document.getElementById('preset-edit-audio_bitrate').value = current.audio_bitrate;
        document.getElementById('preset-edit-skip_crop_detect').checked = current.skip_crop_detect;
        document.getElementById('preset-edit-max_resolution').value = current.max_resolution;
        document.getElementById('preset-edit-form').classList.remove('d-none');
        document.getElementById('preset-import-form').classList.add('d-none');
    }

    startEdit(id) {
        const preset = this.presets.find(p => p.id === id);
        if (!preset) return;
        this.editingId = id;
        document.getElementById('preset-edit-id').value = id;
        document.getElementById('preset-edit-name').value = preset.name;
        document.getElementById('preset-edit-description').value = preset.description || '';
        document.getElementById('preset-edit-crf').value = preset.crf;
        document.getElementById('preset-edit-encoder_preset').value = preset.encoder_preset;
        document.getElementById('preset-edit-svt_params').value = preset.svt_params || '';
        document.getElementById('preset-edit-audio_bitrate').value = preset.audio_bitrate;
        document.getElementById('preset-edit-skip_crop_detect').checked = preset.skip_crop_detect;
        document.getElementById('preset-edit-max_resolution').value = preset.max_resolution;
        document.getElementById('preset-edit-form').classList.remove('d-none');
        document.getElementById('preset-import-form').classList.add('d-none');
    }

    async saveEdit() {
        const payload = {
            name: document.getElementById('preset-edit-name').value.trim(),
            description: document.getElementById('preset-edit-description').value.trim() || null,
            crf: parseInt(document.getElementById('preset-edit-crf').value),
            encoder_preset: parseInt(document.getElementById('preset-edit-encoder_preset').value),
            svt_params: document.getElementById('preset-edit-svt_params').value.trim(),
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
            document.getElementById('preset-edit-form').classList.add('d-none');
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
        if (!confirm('Remove all user presets? Built-in presets will be kept.')) return;
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
            resultEl.textContent = 'Please select a file';
            return;
        }

        try {
            const result = await api.importPresets(fileInput.files[0], strategy);
            resultEl.textContent = `Imported: ${result.imported.length}, Skipped: ${result.skipped.length}, Renamed: ${result.renamed.length}, Errors: ${result.errors.length}`;
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
