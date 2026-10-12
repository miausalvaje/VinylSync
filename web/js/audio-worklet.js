/**
 * AudioWorkletProcessor that batches incoming mono audio into 2048-sample blocks
 * and converts Float32 audio samples to 16-bit signed PCM ArrayBuffers.
 */
class PCM16RecorderProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.bufferSize = 2048;
    this.buffer = new Float32Array(this.bufferSize);
    this.bufferIndex = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) {
      return true;
    }
    const numChannels = input.length;
    const len = input[0].length;

    if (numChannels === 1) {
      const ch0 = input[0];
      for (let i = 0; i < len; i++) {
        this.buffer[this.bufferIndex++] = ch0[i];
        if (this.bufferIndex >= this.bufferSize) {
          this._flushBuffer();
        }
      }
    } else {
      const ch0 = input[0];
      const ch1 = input[1];
      for (let i = 0; i < len; i++) {
        this.buffer[this.bufferIndex++] = (ch0[i] + ch1[i]) * 0.5;
        if (this.bufferIndex >= this.bufferSize) {
          this._flushBuffer();
        }
      }
    }
    return true;
  }

  _flushBuffer() {
    const pcm16 = new Int16Array(this.bufferSize);
    for (let j = 0; j < this.bufferSize; j++) {
      const s = Math.max(-1, Math.min(1, this.buffer[j]));
      pcm16[j] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    this.port.postMessage(pcm16.buffer, [pcm16.buffer]);
    this.bufferIndex = 0;
  }
}

registerProcessor("pcm16-recorder-processor", PCM16RecorderProcessor);
