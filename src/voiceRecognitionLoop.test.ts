import { describe, expect, it } from 'vitest';

import { buildBufferedTranscript, shouldAutoRestartRecognition, shouldScheduleSpeechFlush } from './voiceRecognitionLoop';

describe('voice recognition loop contract', () => {
  it('auto-restarts recognition for transient/no-error endings', () => {
    expect(shouldAutoRestartRecognition({
      shouldListen: true,
      manualStop: false,
      lastError: null,
    })).toBe(true);

    expect(shouldAutoRestartRecognition({
      shouldListen: true,
      manualStop: false,
      lastError: 'aborted',
    })).toBe(true);
  });

  it('does not auto-restart when user explicitly stops or fatal errors occur', () => {
    expect(shouldAutoRestartRecognition({
      shouldListen: false,
      manualStop: false,
      lastError: null,
    })).toBe(false);

    expect(shouldAutoRestartRecognition({
      shouldListen: true,
      manualStop: true,
      lastError: null,
    })).toBe(false);

    expect(shouldAutoRestartRecognition({
      shouldListen: true,
      manualStop: false,
      lastError: 'not-allowed',
    })).toBe(false);
  });

  it('keeps buffered transcript across recognition result events', () => {
    const next = buildBufferedTranscript({
      existingBuffer: 'hello ',
      finalText: 'world',
    });
    expect(next).toBe('hello world ');
  });

  it('continues uninterrupted conversation loop when either interim or buffered speech exists', () => {
    expect(shouldScheduleSpeechFlush({
      bufferedTranscript: 'final words ',
      interimTranscript: '',
    })).toBe(true);

    expect(shouldScheduleSpeechFlush({
      bufferedTranscript: '',
      interimTranscript: 'partial words',
    })).toBe(true);

    expect(shouldScheduleSpeechFlush({
      bufferedTranscript: '   ',
      interimTranscript: '   ',
    })).toBe(false);
  });
});
