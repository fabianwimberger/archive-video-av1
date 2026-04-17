/**
 * Settings panel component
 */
class SettingsPanel {
    constructor() {
        this.presets = [];
        this.selectedPresetId = null;
    }

    async init() {
        await this.loadPresets();
        this.populatePresetSelect();
        this.setupEventListeners();
    }

    async loadPresets() {
        try {
            this.presets = await api.listPresets();
        } catch (error) {
            console.error('Error loading presets:', error);
        }
    }

    populatePresetSelect() {
        const select = document.getElementById('preset-select');
        select.innerHTML = '';

        this.presets.forEach(preset => {
            const option = document.createElement('option');
            option.value = preset.id;
            option.textContent = preset.name + (preset.is_default ? ' (default)' : '');
            if (preset.is_default) {
                option.selected = true;
                this.selectedPresetId = preset.id;
                this.applyPreset(preset.id);
            }
            select.appendChild(option);
        });

        // If no default found, select first
        if (!this.selectedPresetId && this.presets.length > 0) {
            select.value = this.presets[0].id;
            this.selectedPresetId = this.presets[0].id;
            this.applyPreset(this.presets[0].id);
        }
    }

    setupEventListeners() {
        // Preset selector
        document.getElementById('preset-select').addEventListener('change', (e) => {
            this.selectedPresetId = parseInt(e.target.value);
            this.applyPreset(this.selectedPresetId);
            this.checkModified();
        });

        // Auto-estimate buttons
        document.getElementById('btn-auto-estimate').addEventListener('click', () => {
            this.autoEstimate();
        });

        document.getElementById('btn-auto-preset').addEventListener('click', () => {
            this.autoPreset();
        });

        // CRF slider
        const crfSlider = document.getElementById('crf-slider');
        const crfValue = document.getElementById('crf-value');
        crfSlider.addEventListener('input', (e) => {
            crfValue.textContent = e.target.value;
            this.checkModified();
        });

        // Preset slider
        const presetSlider = document.getElementById('preset-slider');
        const presetValue = document.getElementById('preset-value');
        presetSlider.addEventListener('input', (e) => {
            presetValue.textContent = e.target.value;
            this.checkModified();
        });

        // Other inputs
        ['svt-params', 'audio-bitrate'].forEach(id => {
            document.getElementById(id).addEventListener('input', () => this.checkModified());
        });
        document.getElementById('skip-crop').addEventListener('change', () => this.checkModified());
        document.querySelectorAll('input[name="resolution"]').forEach(el => {
            el.addEventListener('change', () => this.checkModified());
        });

        // Convert selected button
        document.getElementById('btn-convert-selected').addEventListener('click', () => {
            window.fileBrowser.convertSelected();
        });
    }

    applyPreset(presetId) {
        const preset = this.presets.find(p => p.id === presetId);
        if (!preset) return;

        document.getElementById('crf-slider').value = preset.crf;
        document.getElementById('crf-value').textContent = preset.crf;
        document.getElementById('preset-slider').value = preset.encoder_preset;
        document.getElementById('preset-value').textContent = preset.encoder_preset;
        document.getElementById('svt-params').value = preset.svt_params || '';
        document.getElementById('audio-bitrate').value = preset.audio_bitrate;
        document.getElementById('skip-crop').checked = preset.skip_crop_detect;

        const maxRes = preset.max_resolution || 1080;
        const resRadio = document.querySelector(`input[name="resolution"][value="${maxRes}"]`);
        if (resRadio) resRadio.checked = true;

        document.getElementById('estimate-info').textContent = '';
        this.checkModified();
    }

    checkModified() {
        if (!this.selectedPresetId) {
            document.getElementById('preset-modified-badge').classList.add('d-none');
            return;
        }
        const preset = this.presets.find(p => p.id === this.selectedPresetId);
        if (!preset) return;

        const current = this.getCurrentSettings();
        const isModified =
            current.crf !== preset.crf ||
            current.encoder_preset !== preset.encoder_preset ||
            current.svt_params !== (preset.svt_params || '') ||
            current.audio_bitrate !== preset.audio_bitrate ||
            current.skip_crop_detect !== preset.skip_crop_detect ||
            current.max_resolution !== preset.max_resolution;

        document.getElementById('preset-modified-badge').classList.toggle('d-none', !isModified);
    }

    updateEstimateButtonState() {
        const btnEstimate = document.getElementById('btn-auto-estimate');
        const btnPreset = document.getElementById('btn-auto-preset');
        const selected = window.fileBrowser ? window.fileBrowser.getSelectedFiles() : [];
        btnEstimate.disabled = selected.length === 0;
        btnPreset.disabled = selected.length === 0;
    }

    async autoEstimate() {
        const selected = window.fileBrowser ? window.fileBrowser.getSelectedFiles() : [];
        if (selected.length === 0) {
            window.app.showNotification('Please select a file first', 'warning');
            return;
        }

        const btn = document.getElementById('btn-auto-estimate');
        const originalHtml = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>';

        try {
            const result = await api.analyzeFile(selected[0]);

            const currentParams = document.getElementById('svt-params').value;
            let params = currentParams;

            params = params.replace(/:?\bfilm-grain=[^:]*/g, '');
            params = params.replace(/:?\bfilm-grain-denoise=[^:]*/g, '');
            params = params.replace(/::+/g, ':');
            params = params.replace(/^:|$/g, '');

            const grainParts = [];
            if (result.film_grain > 0) {
                grainParts.push(`film-grain=${result.film_grain}`);
            }
            if (result.denoise > 0) {
                grainParts.push(`film-grain-denoise=${result.denoise}`);
            }
            if (grainParts.length > 0) {
                params = params ? `${params}:${grainParts.join(':')}` : grainParts.join(':');
            }

            document.getElementById('svt-params').value = params;
            this.checkModified();

            const info = document.getElementById('estimate-info');
            info.innerHTML = `<span class="text-success"><i class="bi bi-check-circle me-1"></i>Estimated: grain=${result.film_grain}, denoise=${result.denoise} (${utils.escapeHtml(result.reason)})</span>`;

            window.app.showNotification(`Estimated: grain=${result.film_grain}, denoise=${result.denoise}`, 'success');
        } catch (error) {
            console.error('Error analyzing file:', error);
            document.getElementById('estimate-info').innerHTML = `<span class="text-danger"><i class="bi bi-exclamation-triangle me-1"></i>Analysis failed: ${utils.escapeHtml(error.message)}</span>`;
            window.app.showNotification(`Analysis failed: ${error.message}`, 'danger');
        } finally {
            btn.innerHTML = originalHtml;
            this.updateEstimateButtonState();
        }
    }

    async autoPreset() {
        const selected = window.fileBrowser ? window.fileBrowser.getSelectedFiles() : [];
        if (selected.length === 0) {
            window.app.showNotification('Please select a file first', 'warning');
            return;
        }

        const btn = document.getElementById('btn-auto-preset');
        const originalHtml = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>';

        try {
            const result = await api.analyzeFile(selected[0], true);
            if (result.suggested_preset_id) {
                const select = document.getElementById('preset-select');
                select.value = result.suggested_preset_id;
                this.selectedPresetId = result.suggested_preset_id;
                this.applyPreset(result.suggested_preset_id);
                window.app.showNotification(`Suggested preset: ${result.reason}`, 'success');
            } else {
                window.app.showNotification('No preset suggestion available', 'warning');
            }
        } catch (error) {
            window.app.showNotification(`Auto-preset failed: ${error.message}`, 'danger');
        } finally {
            btn.innerHTML = originalHtml;
            this.updateEstimateButtonState();
        }
    }

    getCurrentSettings() {
        return {
            crf: parseInt(document.getElementById('crf-slider').value),
            encoder_preset: parseInt(document.getElementById('preset-slider').value),
            svt_params: document.getElementById('svt-params').value,
            audio_bitrate: document.getElementById('audio-bitrate').value,
            skip_crop_detect: document.getElementById('skip-crop').checked,
            max_resolution: parseInt(document.querySelector('input[name="resolution"]:checked').value),
        };
    }

    async convertSingleFile(filePath) {
        const settings = this.getCurrentSettings();
        const preset = this.presets.find(p => p.id === this.selectedPresetId);

        // Determine if we should send preset_id, settings, or both
        const isModified = !document.getElementById('preset-modified-badge').classList.contains('d-none');
        const presetId = this.selectedPresetId;
        const settingsToSend = isModified ? settings : null;

        try {
            const result = await api.createJob(filePath, presetId, settingsToSend);
            if (result.job_ids && result.job_ids.length > 0) {
                console.log(`Created job ${result.job_ids[0]} for ${filePath}`);
                await jobQueue.loadJobs();

                const fileName = filePath.split('/').pop();
                window.app.showNotification(`Conversion started: ${fileName}`, 'success');

                return result;
            }
        } catch (error) {
            console.error('Error creating single file conversion job:', error);
            window.app.showNotification(`Failed to create conversion job: ${error.message}`, 'danger');
            throw error;
        }
    }
}

// Global settings panel instance
const settingsPanel = new SettingsPanel();

// Global function for single file conversion trigger
window.triggerSingleFileConversion = async function(filePath) {
    await settingsPanel.convertSingleFile(filePath);
};
