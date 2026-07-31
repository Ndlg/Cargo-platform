import type {
  RecognitionFormatProfile,
  RecognitionLearningRecord,
} from '../../services/api'

function learningSessionId(profile: RecognitionFormatProfile): string | null {
  const sessionId = profile.provenance?.learning_session_id
  return typeof sessionId === 'string' && sessionId.trim() ? sessionId : null
}

export function learningRecordForProfile(
  profile: RecognitionFormatProfile,
  records: RecognitionLearningRecord[],
): RecognitionLearningRecord | undefined {
  const sessionId = learningSessionId(profile)
  if (!sessionId) return undefined
  return [...records].reverse().find((record) => record.session_id === sessionId)
}

export function withoutProfileLearningRecords(
  profile: RecognitionFormatProfile,
  records: RecognitionLearningRecord[],
): RecognitionLearningRecord[] | null {
  const sessionId = learningSessionId(profile)
  return sessionId
    ? records.filter((record) => record.session_id !== sessionId)
    : null
}
