export type RecognitionErrorCode = string | null | undefined;

const FATAL_ERRORS = new Set(['not-allowed', 'service-not-allowed', 'audio-capture']);

export const shouldAutoRestartRecognition = ({
  shouldListen,
  manualStop,
  lastError,
}: {
  shouldListen: boolean;
  manualStop: boolean;
  lastError: RecognitionErrorCode;
}): boolean => {
  if (!shouldListen || manualStop) return false;
  return !FATAL_ERRORS.has(String(lastError ?? ''));
};

export const buildBufferedTranscript = ({
  existingBuffer,
  finalText,
}: {
  existingBuffer: string;
  finalText: string;
}): string => {
  if (!finalText) return existingBuffer;
  return `${existingBuffer}${finalText} `;
};

export const shouldScheduleSpeechFlush = ({
  bufferedTranscript,
  interimTranscript,
}: {
  bufferedTranscript: string;
  interimTranscript: string;
}): boolean => {
  return Boolean(bufferedTranscript.trim() || interimTranscript.trim());
};
