/* Clean-room streaming mono resampler for Eeveetuber microphone capture. */

class EeveetuberMicrophoneProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const configured = options.processorOptions || {};
    this.targetRate = configured.targetSampleRate;
    this.frameSamples = configured.frameSamples;
    this.rateRatio = sampleRate / this.targetRate;
    this.source = [];
    this.sourcePosition = 0;
    this.frame = new Float32Array(this.frameSamples);
    this.frameOffset = 0;
    this.active = true;
    this.port.onmessage = (event) => {
      if (event.data?.type === "stop") {
        this.active = false;
        this.source.length = 0;
      }
    };
  }

  process(inputs) {
    if (!this.active) return false;
    const channels = inputs[0];
    if (!channels?.length || !channels[0]?.length) return true;

    const sampleCount = channels[0].length;
    for (let index = 0; index < sampleCount; index += 1) {
      let mono = 0;
      for (const channel of channels) mono += channel[index] || 0;
      this.source.push(mono / channels.length);
    }
    this.resampleAvailableInput();
    return true;
  }

  resampleAvailableInput() {
    while (this.sourcePosition + 1 < this.source.length) {
      const leftIndex = Math.floor(this.sourcePosition);
      const fraction = this.sourcePosition - leftIndex;
      const left = this.source[leftIndex];
      const right = this.source[leftIndex + 1];
      this.frame[this.frameOffset] = left + (right - left) * fraction;
      this.frameOffset += 1;
      this.sourcePosition += this.rateRatio;

      if (this.frameOffset === this.frameSamples) {
        const completed = this.frame;
        this.port.postMessage({ type: "pcm.frame", samples: completed.buffer }, [completed.buffer]);
        this.frame = new Float32Array(this.frameSamples);
        this.frameOffset = 0;
      }
    }

    const consumed = Math.min(Math.floor(this.sourcePosition), this.source.length);
    if (consumed > 0) {
      this.source.splice(0, consumed);
      this.sourcePosition -= consumed;
    }
  }
}

registerProcessor("eeveetuber-microphone", EeveetuberMicrophoneProcessor);
