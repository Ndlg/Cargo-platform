export type ParserIssueAction =
  | 'refresh'
  | 'format-learning'
  | 'fingerprint-settings'
  | 'recognition-rule-packs'
  | 'collector-connections'

export type ParserIssueDefinition = {
  label: string
  type: 'warning' | 'error' | 'info'
  actionLabel: string
  action: ParserIssueAction
}

const parserIssueDefinitions: Record<string, ParserIssueDefinition> = {
  compiling_rule: {
    label: '管理员样本正在生成并校验识别规则，请稍后刷新',
    type: 'info',
    actionLabel: '刷新校验状态',
    action: 'refresh',
  },
  rule_replay_failed: {
    label: '确认结果未能生成可复用规则，请修正学习样本后重试',
    type: 'error',
    actionLabel: '重新学习这种格式',
    action: 'format-learning',
  },
  fingerprint_adapter_required: {
    label: '系统尚未支持该格式，请联系维护人员接入后刷新',
    type: 'warning',
    actionLabel: '刷新支持状态',
    action: 'refresh',
  },
  fingerprint_field_selection_required: {
    label: '当前面单格式尚未选择用于生成规则的字段',
    type: 'warning',
    actionLabel: '选择学习字段',
    action: 'fingerprint-settings',
  },
  format_profile_missing: {
    label: '当前格式还没有可复用的识别规则',
    type: 'warning',
    actionLabel: '学习这种面单格式',
    action: 'format-learning',
  },
  profile_ambiguous: {
    label: '多个已学习规则产生不同结果，需要重新学习这种格式',
    type: 'warning',
    actionLabel: '重新学习这种面单格式',
    action: 'format-learning',
  },
  format_profile_incomplete: {
    label: '现有识别规则未能生成完整商品行',
    type: 'warning',
    actionLabel: '重新学习这种面单格式',
    action: 'format-learning',
  },
  capture_source_exception: {
    label: '采集源数据异常，面单已保留并隔离',
    type: 'warning',
    actionLabel: '检查采集连接',
    action: 'collector-connections',
  },
  rule_pack_missing: {
    label: '当前工作空间没有启用识别规则包',
    type: 'warning',
    actionLabel: '学习第一种面单格式',
    action: 'format-learning',
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
    actionLabel: '学习这种面单格式',
    action: 'format-learning',
  }
}

export function parserIssueRoute(action: ParserIssueAction): string | null {
  if (action === 'collector-connections') return '/admin/collector-connections'
  if (action === 'fingerprint-settings') return '/admin/fingerprint-settings'
  if (action === 'recognition-rule-packs') return '/admin/recognition-rule-packs'
  if (action === 'format-learning') return '/admin/format-learning'
  return null
}
