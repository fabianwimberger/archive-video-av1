/**
 * Settings panel component
 */
class SettingsPanel {
    constructor() {
        this.presets = null;
    }

    async init() {
        await this.loadPresets();
        this.setupEventListeners();
        this.applyMode('default');
    }

    async loadPresets() {
        try {
            this.presets = await api.getPresets();
        } catch (error) {
            console.error('Error loading presets:', error);
        }
    }

    setupEventListeners() {
        // Mode selector
        document.getElementById('mode-select').addEventListener('change', (e) => {
            this.applyMode(e.target.value);
        });

        // Auto-estimate button
        document.getElementById('btn-auto-estimate').addEventListener('click', () => {
            this.autoEstimate();
        });

        // CRF slider
        const crfSlider = document.getElementById('crf-slider');
        const crfValue = document.getElementById('crf-value');
        crfSlider.addEventListener('input', (e) => {
            crfValue.textContent = e.target.value;
        });

        // Preset slider
        const presetSlider = document.getElementById('preset-slider');
        const presetValue = document.getElementById('preset-value');
        presetSlider.addEventListener('input', (e) => {
            presetValue.textContent = e.target.value;
        });

        // Convert selected button
        document.getElementById('btn-convert-selected').addEventListener('click', () => {
            window.fileBrowser.convertSelected();
        });
    }

    applyMode(mode) {
        const preset = this.presets ? this.presets[mode] : null;

        if (preset) {
            document.getElementById('crf-slider').value = preset.crf;
            document.getElementById('crf-value').textContent = preset.crf;
            document.getElementById('preset-slider').value = preset.preset;
            document.getElementById('preset-value').textContent = preset.preset;
            document.getElementById('svt-params').value = preset.svt_params;
            document.getElementById('audio-bitrate').value = preset.audio_bitrate;
            document.getElementById('skip-crop').checked = preset.skip_crop_detect;

            // Apply max_resolution (default to 1080 if not in preset)
            const maxRes = preset.max_resolution || 1080;
            const resRadio = document.querySelector(`input[name="resolution"][value="${maxRes}"]`);
            if (resRadio) resRadio.checked = true;
        }

        // Clear estimate info when switching modes
        document.getElementById('estimate-info').textContent = '';
    }

    updateEstimateButtonState() {
        const btn = document.getElementById('btn-auto-estimate');
        const selected = window.fileBrowser ? window.fileBrowser.getSelectedFiles() : [];
        const mode = document.getElementById('mode-select').value;
        btn.disabled = selected.length === 0 || mode !== 'default';
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
            // Analyze the first selected file
            const result = await api.analyzeFile(selected[0]);

            // Build SVT params based on estimate, preserving other params like tune=0
            const currentParams = document.getElementById('svt-params').value;
            let params = currentParams;

            // Remove existing film-grain and film-grain-denoise params
            params = params.replace(/:?\bfilm-grain=[^:]*/g, '');
            params = params.replace(/:?\bfilm-grain-denoise=[^:]*/g, '');
            // Clean up empty segments
            params = params.replace(/::+/g, ':');
            params = params.replace(/^:|:$/g, '');

            // Add estimated params
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

    getCurrentSettings() {
        return {
            mode: document.getElementById('mode-select').value,
            crf: parseInt(document.getElementById('crf-slider').value),
            preset: parseInt(document.getElementById('preset-slider').value),
            svt_params: document.getElementById('svt-params').value,
            audio_bitrate: document.getElementById('audio-bitrate').value,
            skip_crop_detect: document.getElementById('skip-crop').checked,
            max_resolution: parseInt(document.querySelector('input[name="resolution"]:checked').value),
        };
    }

    async convertSingleFile(filePath) {
        const settings = this.getCurrentSettings();

        try {
            const result = await api.createJob(filePath, settings.mode, settings);
            if (result.job_ids && result.job_ids.length > 0) {
                console.log(`Created job ${result.job_ids[0]} for ${filePath}`);
                // Reload job queue to show the new job
                await jobQueue.loadJobs();

                // Show success notification
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
