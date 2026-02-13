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
