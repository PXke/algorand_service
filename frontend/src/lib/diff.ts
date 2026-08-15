// Line-based diff (longest common subsequence) for comparing two article
// version bodies in the admin panel. No external dependency -- LCS on lines
// is a well-understood, small algorithm and this repo has no JS test runner
// to verify a heavier library's integration against, so keeping this
// self-contained and easy to eyeball-verify beats adding a new dependency
// at the end of an unsupervised session.

export type DiffOp = { kind: 'equal' | 'add' | 'remove'; line: string }

/** Longest common subsequence table over two line arrays, then walk it backwards into a sequence of equal/add/remove ops. */
export function diffLines(a: string[], b: string[]): DiffOp[] {
  const n = a.length
  const m = b.length
  // dp[i][j] = LCS length of a[i..] and b[j..]
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }
  const ops: DiffOp[] = []
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      ops.push({ kind: 'equal', line: a[i] })
      i++
      j++
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      ops.push({ kind: 'remove', line: a[i] })
      i++
    } else {
      ops.push({ kind: 'add', line: b[j] })
      j++
    }
  }
  while (i < n) {
    ops.push({ kind: 'remove', line: a[i] })
    i++
  }
  while (j < m) {
    ops.push({ kind: 'add', line: b[j] })
    j++
  }
  return ops
}

/** Split text into diff-able lines the same way for both sides. */
export function toLines(text: string): string[] {
  return (text ?? '').split('\n')
}
