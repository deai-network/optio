interface ProgressInfo {
  percent: number;
  message?: string;
}

interface ProcessLike {
  _id?: string;
  progressMode: 'self' | 'children';
  childWeights?: {
    method: 'equal' | 'weighted';
    weights?: Record<string, number>;
    timing: 'parallel' | 'sequential';
  };
  progress: ProgressInfo;
}

interface ChildLike {
  _id?: string;
  progress: ProgressInfo;
}

export function computeAggregatedProgress(
  process: ProcessLike,
  children: ChildLike[],
): ProgressInfo {
  if (process.progressMode === 'self' || children.length === 0) {
    return process.progress;
  }

  const weights = process.childWeights;
  let percent: number;

  if (weights?.method === 'weighted' && weights.weights) {
    const w = weights.weights;
    let totalWeight = 0;
    let weightedSum = 0;
    for (const child of children) {
      const childWeight = w[child._id ?? ''] ?? 1;
      totalWeight += childWeight;
      weightedSum += child.progress.percent * childWeight;
    }
    percent = totalWeight > 0 ? weightedSum / totalWeight : 0;
  } else {
    percent = children.reduce((sum, c) => sum + c.progress.percent, 0) / children.length;
  }

  return { percent: Math.round(percent * 100) / 100, message: process.progress.message };
}
