export type EditableLearningRow = {
  product: string
  sales_attr1: string
  sales_attr2: string
  quantity: string | number
  remark: string
}

export type LearningRow = Omit<EditableLearningRow, 'quantity'> & { quantity: number }

export type PreparedLearningRows =
  | { ok: true; rows: LearningRow[] }
  | { ok: false; message: string }

type LearningResultLike = {
  warnings?: unknown
  reruns?: unknown
}

export function prepareLearningRows(rows: EditableLearningRow[]): PreparedLearningRows {
  if (!rows.length) return { ok: false, message: '至少保留一条商品行' }

  const prepared: LearningRow[] = []
  for (const [index, row] of rows.entries()) {
    const product = row.product.trim()
    if (!product) return { ok: false, message: `第 ${index + 1} 行商品不能为空` }

    const quantity = Number(row.quantity)
    if (!Number.isInteger(quantity) || quantity < 1) {
      return { ok: false, message: `第 ${index + 1} 行数量必须是大于 0 的整数` }
    }

    prepared.push({
      product,
      sales_attr1: row.sales_attr1.trim(),
      sales_attr2: row.sales_attr2.trim(),
      quantity,
      remark: row.remark.trim(),
    })
  }
  return { ok: true, rows: prepared }
}

export function learningResultWarnings(result: LearningResultLike): string[] {
  const messages = new Set<string>()
  if (Array.isArray(result.warnings)) {
    for (const warning of result.warnings) {
      if (typeof warning === 'string' && warning.trim()) messages.add(warning.trim())
    }
  }

  if (Array.isArray(result.reruns)) {
    for (const rerun of result.reruns) {
      if (!rerun || typeof rerun !== 'object') continue
      const record = rerun as Record<string, unknown>
      if (record.status !== 'failed') continue
      const error = typeof record.error === 'string' && record.error.trim()
        ? `：${record.error.trim()}`
        : ''
      messages.add(`相关采集轮次重算失败${error}`)
    }
  }

  return [...messages]
}

export function isFingerprintFieldsMissingError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error ?? '')
  return /尚未配置该面单指纹的学习字段|未配置.*面单指纹.*字段/.test(message)
}
