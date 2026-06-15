export type WodaStructureKind =
  | 'single_line_standard'
  | 'reverse_field_order'
  | 'multi_item'
  | 'remark_product'
  | 'unknown'

export type WodaStructureConfidence = 'high' | 'medium' | 'low'

export type WodaExtractedItem = {
  productText: string
  salesAttr1Text?: string
  salesAttr2Text?: string
  remarkText?: string
  specText: string
  sizeText: string
  quantityText: string
  rawText: string
}

export type WodaStructureResult = {
  kind: WodaStructureKind
  label: string
  confidence: WodaStructureConfidence
  lines: string[]
  itemCount: number
  items: WodaExtractedItem[]
  reason: string
}

const STRUCTURE_LABELS: Record<WodaStructureKind, string> = {
  single_line_standard: '单行标准',
  reverse_field_order: '属性在前',
  multi_item: '多商品',
  remark_product: '备注商品',
  unknown: '未识别结构',
}

const STRUCTURE_KIND_SET = new Set<WodaStructureKind>([
  'single_line_standard',
  'reverse_field_order',
  'multi_item',
  'remark_product',
  'unknown',
])

const SIZE_TEXT = '(?:[2-4]\\d(?:\\.5)?|50)'
const QTY_PATTERN = /(?:\*|x|X|×)\s*(\d+)/g
const SIZE_QTY_PATTERN = new RegExp(`(?:^|[^\\d])(${SIZE_TEXT})\\s*(?:\\*|x|X|×)\\s*(\\d+)(?!\\d)`)
const NAMED_SIZE_PATTERN = new RegExp(`(?:销售属性2|属性2|鞋码|尺码|码)[:：\\s]*(${SIZE_TEXT})(?!\\d)`)
const LOOSE_SIZE_PATTERN = new RegExp(`(?:^|[^\\d])(${SIZE_TEXT})(?!\\d)`)
const REMARK_FIELD_PATTERN = /(颜色分类|商品名称|商品名|销售属性1|销售属性2|属性1|属性2|规格|鞋码|尺码|买家备注|卖家备注|卖家留言|备注|留言|color|size|sku)/i
const IGNORED_LINE_PATTERN = /(运单号|物流单号|快递单号|YT\d{8,}|\[[A-Z]{1,4}\d{8,})/i

function valueText(value: unknown): string {
  if (value === null || value === undefined || value === '') return ''
  if (Array.isArray(value)) return value.map((item) => valueText(item)).filter(Boolean).join('\n')
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function samplePreviewText(config: Record<string, unknown>, fieldCode: string): string {
  return valueText(objectValue(config.sample_preview)[fieldCode])
}

function cleanText(value: unknown): string {
  return valueText(value)
    .replace(/[\u200e\u200f\u202a-\u202e\u2066-\u2069]/g, '')
    .replace(/\r\n/g, '\n')
    .trim()
}

function splitLines(value: unknown): string[] {
  return cleanText(value)
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line && !IGNORED_LINE_PATTERN.test(line))
}

function splitParts(line: string): string[] {
  return line
    .replace(/[；;]/g, '，')
    .split(/[，,、]+/)
    .map((part) => part.trim())
    .filter(Boolean)
}

function countQuantityMarkers(text: string): number {
  return [...cleanText(text).matchAll(QTY_PATTERN)].length
}

function hasQuantity(text: string): boolean {
  return countQuantityMarkers(text) > 0
}

function isRemarkLine(text: string): boolean {
  return REMARK_FIELD_PATTERN.test(text)
}

function extractQuantity(text: string): string {
  const match = cleanText(text).match(/(?:\*|x|X|×)\s*(\d+)/)
  return match?.[1] ?? ''
}

function splitQuantityTail(text: string): { segmentText: string; quantityText: string } | null {
  const match = cleanText(text).match(/(.+?)\s*(?:\*|x|X|×)\s*(\d+)\s*$/)
  if (!match?.[1] || !match?.[2]) return null
  return {
    segmentText: match[1].trim(),
    quantityText: match[2].trim(),
  }
}

function extractSize(text: string): string {
  const cleaned = cleanText(text)
  const named = cleaned.match(NAMED_SIZE_PATTERN)
  if (named?.[1]) return named[1]
  const sizeQty = cleaned.match(SIZE_QTY_PATTERN)
  if (sizeQty?.[1]) return sizeQty[1]
  const loose = cleaned.match(LOOSE_SIZE_PATTERN)
  return loose?.[1] ?? ''
}

function stripQuantitySuffix(text: string): string {
  return cleanText(text)
    .replace(new RegExp(`[，,、\\s]*${SIZE_TEXT}?\\s*(?:\\*|x|X|×)\\s*\\d+\\s*$`), '')
    .replace(/[，,、；;:\s]+$/g, '')
    .trim()
}

function stripSizeFromSpec(text: string, size: string): string {
  if (!size) return cleanText(text)
  return cleanText(text)
    .replace(new RegExp(`[，,、\\s]*${size}\\s*$`), '')
    .replace(/[，,、；;:\s]+$/g, '')
    .trim()
}

function namedValue(text: string, names: string[]): string {
  const escapedNames = names.map((name) => name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')
  const match = cleanText(text).match(new RegExp(`(?:${escapedNames})[:：]\\s*([^;；\\n]+)`))
  return match?.[1]?.trim() ?? ''
}

function remarkTextFromLines(lines: string[]): string {
  return lines.filter(isRemarkLine).map(cleanText).filter(Boolean).join('\n')
}

function itemFromSingleStandardLine(line: string): WodaExtractedItem | null {
  const parts = splitParts(line)
  if (parts.length < 2) return null
  if (parts.length === 2 && hasQuantity(parts[1])) {
    const salesAttr2Text = extractSize(parts[1])
    const salesAttr1Text = stripQuantitySuffix(parts[1])
    return {
      productText: parts[0],
      salesAttr1Text,
      salesAttr2Text,
      specText: salesAttr1Text,
      sizeText: salesAttr2Text,
      quantityText: extractQuantity(parts[1]),
      rawText: line,
    }
  }
  if (parts.length < 3) return null
  const tail = parts[parts.length - 1]
  const tailQuantity = splitQuantityTail(tail)
  if (!tailQuantity) return null
  const sizeQty = tail.match(SIZE_QTY_PATTERN) || line.match(SIZE_QTY_PATTERN)
  const salesAttr1Text = parts.slice(1, -1).join('，')
  const salesAttr2Text = sizeQty?.[1] || tailQuantity.segmentText
  const quantityText = sizeQty?.[2] || tailQuantity.quantityText
  return {
    productText: parts[0],
    salesAttr1Text,
    salesAttr2Text,
    specText: salesAttr1Text,
    sizeText: salesAttr2Text,
    quantityText,
    rawText: line,
  }
}

function itemFromReverseText(lines: string[]): WodaExtractedItem | null {
  if (lines.length === 1) {
    const parts = splitParts(lines[0])
    if (parts.length !== 2 || !hasQuantity(parts[1])) return null
    const productCandidate = stripQuantitySuffix(parts[1])
    if (productCandidate.length < 8 || productCandidate.length <= parts[0].length) return null
    const size = extractSize(parts[0]) || extractSize(parts[1])
    return {
      productText: productCandidate,
      specText: stripSizeFromSpec(parts[0], size),
      sizeText: size,
      quantityText: extractQuantity(parts[1]),
      rawText: lines[0],
    }
  }

  if (lines.length !== 2) return null
  const [first, second] = lines
  if (hasQuantity(first) || !hasQuantity(second) || isRemarkLine(first) || isRemarkLine(second)) return null
  const size = extractSize(first) || extractSize(second)
  return {
    productText: stripQuantitySuffix(second),
    specText: stripSizeFromSpec(first, size),
    sizeText: size,
    quantityText: extractQuantity(second),
    rawText: `${first}\n${second}`,
  }
}

function itemFromRemark(lines: string[]): WodaExtractedItem | null {
  const remarkLines = lines.filter(isRemarkLine)
  if (!remarkLines.length) return null
  const productLine =
    lines.find((line) => !isRemarkLine(line) && hasQuantity(line)) ??
    lines.find((line) => !isRemarkLine(line)) ??
    ''
  const remarkLine = remarkLines[0]
  const productFromRemark = namedValue(remarkLine, ['商品名称', '商品名', '商品'])
  const productFromLine = stripQuantitySuffix(productLine)
  const specFromRemark = namedValue(remarkLine, ['销售属性1', '属性1', '规格', '颜色分类'])
  const salesAttr2FromRemark = namedValue(remarkLine, ['销售属性2', '属性2', '鞋码', '尺码', '码'])
  const remarkText = remarkTextFromLines(remarkLines)
  return {
    productText: productFromRemark || productFromLine || cleanText(remarkLine),
    specText: specFromRemark || productFromLine,
    salesAttr1Text: specFromRemark || productFromLine,
    salesAttr2Text: salesAttr2FromRemark || extractSize(remarkLine) || extractSize(productLine),
    remarkText,
    sizeText: salesAttr2FromRemark || extractSize(remarkLine) || extractSize(productLine),
    quantityText: extractQuantity(productLine) || '1',
    rawText: lines.join('\n'),
  }
}

function itemsFromMulti(lines: string[]): WodaExtractedItem[] {
  const productLines = lines
    .map((line, lineIndex) => ({ line, lineIndex }))
    .filter(({ line }) => hasQuantity(line))
  const remarkLines = lines.filter(isRemarkLine)
  const items = productLines.map(({ line, lineIndex }, index) => {
    const standard = itemFromSingleStandardLine(line)
    if (standard) {
      const remarkLine = remarkLines[index]
      if (remarkLine) {
        return {
          ...standard,
          remarkText: cleanText(remarkLine),
          rawText: `${line}\n${remarkLine}`,
        }
      }
      return standard
    }

    const previousLine = lineIndex > 0 ? lines[lineIndex - 1] : ''
    if (previousLine && !hasQuantity(previousLine) && !isRemarkLine(previousLine)) {
      const reverseItem = itemFromReverseText([previousLine, line])
      if (reverseItem) {
        const remarkLine = remarkLines[index]
        return remarkLine
          ? {
              ...reverseItem,
              remarkText: cleanText(remarkLine),
              rawText: `${reverseItem.rawText}\n${remarkLine}`,
            }
          : reverseItem
      }
    }

    const remarkLine = remarkLines[index]
    if (remarkLine) {
      return itemFromRemark([line, remarkLine]) ?? {
        productText: stripQuantitySuffix(line),
        remarkText: cleanText(remarkLine),
        specText: '',
        sizeText: extractSize(line),
        quantityText: extractQuantity(line),
        rawText: line,
      }
    }

    return {
      productText: stripQuantitySuffix(line),
      remarkText: '',
      specText: '',
      sizeText: extractSize(line),
      quantityText: extractQuantity(line),
      rawText: line,
    }
  })

  return items.filter((item) => item.productText || item.specText || item.sizeText || item.quantityText)
}

function hasRemarkProductShape(lines: string[]): boolean {
  if (!lines.some(isRemarkLine)) return false
  return lines.some((line) => {
    if (!hasQuantity(line)) return false
    const parts = splitParts(line)
    if (parts.length < 3) return false
    const tail = parts[parts.length - 1]
    return Boolean(splitQuantityTail(tail) && !extractSize(tail))
  })
}

function result(
  kind: WodaStructureKind,
  lines: string[],
  items: WodaExtractedItem[],
  confidence: WodaStructureConfidence,
  reason: string,
): WodaStructureResult {
  const normalizedItems = items.map((item) => ({
    ...item,
    salesAttr1Text: item.salesAttr1Text ?? item.specText,
    salesAttr2Text: item.salesAttr2Text ?? item.sizeText,
  }))
  return {
    kind,
    label: STRUCTURE_LABELS[kind],
    confidence,
    lines,
    itemCount: Math.max(normalizedItems.length, kind === 'unknown' ? 0 : 1),
    items: normalizedItems,
    reason,
  }
}

export function analyzeWodaStructure(value: unknown): WodaStructureResult {
  const lines = splitLines(value)
  const rawText = lines.join('\n')
  if (!lines.length) return result('unknown', [], [], 'low', '没有可识别文字')

  const quantityCount = countQuantityMarkers(rawText)
  const quantityLineCount = lines.filter(hasQuantity).length
  const remarkLineCount = lines.filter(isRemarkLine).length
  if (remarkLineCount >= 1 && hasRemarkProductShape(lines)) {
    return result('remark_product', lines, itemsFromMulti(lines), 'high', '商品行带备注补充，按备注商品结构识别')
  }

  if (quantityCount > 1 || quantityLineCount > 1 || (quantityCount >= 1 && remarkLineCount > 1)) {
    return result('multi_item', lines, itemsFromMulti(lines), 'high', '出现多个数量或多条备注商品信息')
  }

  const remarkItem = itemFromRemark(lines)
  if (remarkItem && remarkLineCount >= 1) {
    const reason = lines.some((line) => !isRemarkLine(line))
      ? '商品行带备注补充，按备注商品结构识别'
      : '商品信息需要从备注/自定义字段中读取'
    return result('remark_product', lines, [remarkItem], 'high', reason)
  }

  const reverseItem = itemFromReverseText(lines)
  if (reverseItem) {
    return result('reverse_field_order', lines, [reverseItem], 'high', '属性在前，商品和数量在后')
  }

  if (lines.length === 1) {
    const standardItem = itemFromSingleStandardLine(lines[0])
    if (standardItem) {
      return result('single_line_standard', lines, [standardItem], 'high', '单行包含商品、销售属性和数量')
    }
  }

  return result('unknown', lines, [], 'low', '没有命中已知商品结构')
}

function asStructureKind(value: unknown): WodaStructureKind | null {
  const text = valueText(value)
  return STRUCTURE_KIND_SET.has(text as WodaStructureKind) ? text as WodaStructureKind : null
}

function inferKindFromConfigFields(config: Record<string, unknown> | null | undefined): WodaStructureKind | null {
  if (!config) return null
  const productText = valueText(config.product_segment_text) || samplePreviewText(config, 'custom_product_text')
  const specText = valueText(config.spec_segment_text) || samplePreviewText(config, 'custom_sales_attr1_text')
  const sizeText = valueText(config.size_segment_text) || samplePreviewText(config, 'custom_sales_attr2_text')
  const salesAttr1Text = valueText(config.sales_attr1_segment_text) || samplePreviewText(config, 'custom_sales_attr1_text')
  const salesAttr2Text = valueText(config.sales_attr2_segment_text) || samplePreviewText(config, 'custom_sales_attr2_text')
  const quantityText = valueText(config.quantity_segment_text) || samplePreviewText(config, 'custom_quantity_text')
  const productLineIndex = Number(config.product_line_index ?? -1)
  const specLineIndex = Number(config.spec_line_index ?? -1)
  const attr1Text = salesAttr1Text || specText
  const attr2Text = salesAttr2Text || sizeText

  if (isRemarkLine(attr1Text) || isRemarkLine(productText)) return 'remark_product'
  if (hasQuantity(productText) && attr1Text) return 'reverse_field_order'
  if (productLineIndex >= 0 && specLineIndex >= 0 && productLineIndex !== specLineIndex && attr1Text) {
    return 'remark_product'
  }
  if (productText && attr1Text && attr2Text && quantityText) return 'single_line_standard'
  if (productText && attr1Text && quantityText) return 'single_line_standard'
  return null
}

export function inferWodaTemplateKind(
  config: Record<string, unknown> | null | undefined,
  sampleText?: unknown,
): WodaStructureKind {
  const explicit = asStructureKind(config?.match_structure_kind)
  if (explicit) return explicit

  const fieldKind = inferKindFromConfigFields(config)
  if (fieldKind) return fieldKind

  const sampleResult = analyzeWodaStructure(sampleText)
  return sampleResult.kind
}

export function wodaStructureLabel(kind: WodaStructureKind): string {
  return STRUCTURE_LABELS[kind]
}
