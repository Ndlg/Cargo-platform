export type ParserIssueAction =
  | 'refresh'
  | 'ai-recognition'
  | 'fingerprint-settings'
  | 'recognition-rule-packs'

export type ParserIssueDefinition = {
  label: string
  type: 'warning' | 'error' | 'info'
  actionLabel: string
  action: ParserIssueAction
}

const parserIssueDefinitions: Record<string, ParserIssueDefinition> = {
  model_running: {
    label: 'AI 正在识别当前面单，请稍后刷新',
    type: 'info',
    actionLabel: '刷新识别状态',
    action: 'refresh',
  },
  approving: {
    label: '管理员确认正在生成识别规则，请稍后刷新',
    type: 'info',
    actionLabel: '刷新识别状态',
    action: 'refresh',
  },
  ai_rule_pending: {
    label: '新格式待管理员确认',
    type: 'warning',
    actionLabel: '打开 AI 面单解析',
    action: 'ai-recognition',
  },
  candidate_invalid: {
    label: 'AI 候选结果不完整，请修正五个业务字段后确认',
    type: 'warning',
    actionLabel: '修正 AI 识别结果',
    action: 'ai-recognition',
  },
  ai_rule_invalid: {
    label: '管理员确认未生成有效识别规则，请修正后重试',
    type: 'error',
    actionLabel: '重新生成识别规则',
    action: 'ai-recognition',
  },
  ai_result_invalid: {
    label: 'AI 返回结果不符合五字段要求，请修正后确认',
    type: 'warning',
    actionLabel: '修正 AI 识别结果',
    action: 'ai-recognition',
  },
  ai_unavailable: {
    label: 'AI 识别服务不可用，已固化规则仍可继续使用',
    type: 'error',
    actionLabel: '打开 AI 面单解析',
    action: 'ai-recognition',
  },
  ai_parse_failed: {
    label: 'AI 识别结果处理失败，请检查当前面单字段后重试',
    type: 'error',
    actionLabel: '重新解析当前面单',
    action: 'ai-recognition',
  },
  fingerprint_adapter_required: {
    label: '系统尚未支持该格式，请联系维护人员接入后刷新',
    type: 'warning',
    actionLabel: '刷新支持状态',
    action: 'refresh',
  },
  fingerprint_field_selection_required: {
    label: '当前面单格式尚未选择提供给 AI 的字段',
    type: 'warning',
    actionLabel: '选择提供给 AI 的字段',
    action: 'fingerprint-settings',
  },
  format_profile_missing: {
    label: '当前格式还没有可复用的识别规则',
    type: 'warning',
    actionLabel: '学习这种面单格式',
    action: 'ai-recognition',
  },
  format_profile_incomplete: {
    label: '现有识别规则未能生成完整商品行',
    type: 'warning',
    actionLabel: '重新学习这种面单格式',
    action: 'ai-recognition',
  },
  rule_pack_missing: {
    label: '当前工作空间没有启用识别规则包',
    type: 'warning',
    actionLabel: '学习第一种面单格式',
    action: 'ai-recognition',
  },
  rule_pack_invalid: {
    label: '当前识别规则包不可用于面单解析',
    type: 'error',
    actionLabel: '检查识别规则包',
    action: 'recognition-rule-packs',
  },
}

export function parserIssueFor(
  status: string,
  message = '',
  hasUnresolvedWaybills = false,
): ParserIssueDefinition | null {
  const knownIssue = parserIssueDefinitions[status]
  if (knownIssue) return knownIssue
  if ((!status || status === 'parsed') && !hasUnresolvedWaybills) return null
  return {
    label: message || '当前面单未生成可用商品行',
    type: 'warning',
    actionLabel: '打开 AI 面单解析',
    action: 'ai-recognition',
  }
}

export function parserIssueRoute(action: ParserIssueAction): string | null {
  if (action === 'fingerprint-settings') return '/admin/fingerprint-settings'
  if (action === 'recognition-rule-packs') return '/admin/recognition-rule-packs'
  if (action === 'ai-recognition') return '/admin/ai-recognition'
  return null
}
