import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Calendar, Play, RefreshCw, CheckCircle2, FileText, XCircle, Clock } from 'lucide-react';
import { analysisApi, portfolioDigestApi, watchlistApi } from '../api/analysis';
import { systemConfigApi } from '../api/systemConfig';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import { ApiErrorAlert, Badge, Button, EmptyState, InlineAlert } from '../components/common';
import { AppPage } from '../components/common/AppPage';
import type { PortfolioDigestResponse, StockFreshness, TaskStatus } from '../types/analysis';

type TaskState = {
  stockCode: string;
  taskId: string;
  status: 'pending' | 'processing' | 'completed' | 'failed';
  error?: string;
};

const EodSummaryPage: React.FC = () => {
  // Dates
  const [availableDates, setAvailableDates] = useState<string[]>([]);
  const [selectedDate, setSelectedDate] = useState<string>(() => new Date().toISOString().slice(0, 10));

  // Stock list
  const [stockCodes, setStockCodes] = useState<string[]>([]);
  const [loadingStocks, setLoadingStocks] = useState(true);

  // Workflow state
  const [phase, setPhase] = useState<'idle' | 'analyzing' | 'digesting' | 'done'>('idle');
  const [tasks, setTasks] = useState<TaskState[]>([]);
  const [digest, setDigest] = useState<PortfolioDigestResponse | null>(null);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const pollTimer = useRef<number | null>(null);

  // ---- Load stock list from watchlist ----
  const loadStockList = useCallback(async () => {
    setLoadingStocks(true);
    try {
      const result = await watchlistApi.list();
      const codes = result.items.map((i) => i.code);
      setStockCodes(codes);
    } catch {
      // Fall back to system config
      try {
        const config = await systemConfigApi.getConfig(false);
        const stockItem = config.items?.find((i) => i.key === 'STOCK_LIST');
        if (stockItem?.value) {
          const codes = stockItem.value.split(',').map((c) => c.trim()).filter(Boolean);
          setStockCodes(codes);
        }
      } catch { /* silent */ }
    } finally {
      setLoadingStocks(false);
    }
  }, []);

  // ---- Load available dates ----
  const loadAvailableDates = useCallback(async () => {
    try {
      const result = await portfolioDigestApi.getAvailableDates();
      setAvailableDates(result.dates);
    } catch { /* silent */ }
  }, []);

  useEffect(() => {
    void loadStockList();
    void loadAvailableDates();
  }, [loadStockList, loadAvailableDates]);

  // ---- Cleanup poll on unmount ----
  useEffect(() => {
    return () => {
      if (pollTimer.current !== null) window.clearInterval(pollTimer.current);
    };
  }, []);

  // ---- Poll task statuses ----
  const pollTasks = useCallback(async (taskList: TaskState[]) => {
    const updated = await Promise.all(
      taskList.map(async (t) => {
        if (t.status === 'completed' || t.status === 'failed') return t;
        try {
          const status: TaskStatus = await analysisApi.getStatus(t.taskId);
          return {
            ...t,
            status: status.status === 'completed' ? 'completed' as const
                  : status.status === 'failed' || status.status === 'cancelled' ? 'failed' as const
                  : 'processing' as const,
            error: status.error,
          };
        } catch {
          return { ...t, status: 'failed' as const, error: 'Status check failed' };
        }
      })
    );
    return updated;
  }, []);

  // ---- Run full workflow ----
  const runFullWorkflow = useCallback(async () => {
    if (stockCodes.length === 0) return;

    setPhase('analyzing');
    setError(null);
    setDigest(null);

    try {
      // Submit batch analysis
      const response = await analysisApi.analyzeAsync({
        stockCodes,
        reportType: 'full',
        asyncMode: true,
        reportLanguage: 'zh',
      });

      // Extract task IDs
      let taskList: TaskState[] = [];
      if ('accepted' in response && Array.isArray(response.accepted)) {
        taskList = response.accepted.map((item) => ({
          stockCode: item.stockCode,
          taskId: item.taskId,
          status: 'processing' as const,
        }));
      } else if ('taskId' in response) {
        taskList = [{
          stockCode: stockCodes[0],
          taskId: (response as { taskId: string }).taskId,
          status: 'processing' as const,
        }];
      }

      setTasks(taskList);

      // Poll until all done
      await new Promise<void>((resolve) => {
        const interval = window.setInterval(async () => {
          const updated = await pollTasks(taskList);
          setTasks(updated);
          taskList = updated;

          const allDone = updated.every((t) => t.status === 'completed' || t.status === 'failed');
          if (allDone) {
            window.clearInterval(interval);
            resolve();
          }
        }, 3000);

        pollTimer.current = interval;
      });

      // All done — generate digest
      setPhase('digesting');
      const result = await portfolioDigestApi.generate({ date: selectedDate, lang: 'zh' });
      setDigest(result);
      setPhase('done');
      void loadAvailableDates(); // Refresh date list

    } catch (err) {
      setError(getParsedApiError(err));
      setPhase('idle');
    }
  }, [stockCodes, selectedDate, pollTasks, loadAvailableDates]);

  // ---- Generate digest only (for past dates) ----
  const generateDigestOnly = useCallback(async (date?: string) => {
    setPhase('digesting');
    setError(null);
    try {
      const result = await portfolioDigestApi.generate({ date: date || selectedDate, lang: 'zh' });
      setDigest(result);
      setPhase('done');
    } catch (err) {
      setError(getParsedApiError(err));
      setPhase('idle');
    }
  }, [selectedDate]);

  // ---- Format date ----
  const formatDate = (dateStr: string) => {
    try {
      const d = new Date(dateStr + 'T00:00:00');
      return d.toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'short' });
    } catch { return dateStr; }
  };

  // ---- Freshness badges ----
  const FreshnessBadges: React.FC<{ freshness: StockFreshness[] }> = ({ freshness }) => {
    if (!freshness?.length) return null;
    return (
      <div className="flex flex-wrap gap-2 mb-4">
        {freshness.map((f) => (
          <Badge key={f.code} variant={f.fresh ? 'success' : f.ageDays === 1 ? 'warning' : 'danger'}>
            {f.code}{f.fresh ? ' 今日' : f.ageDays === 1 ? ' 昨日' : ` ${f.ageDays}天前`}
          </Badge>
        ))}
      </div>
    );
  };

  const isToday = selectedDate === new Date().toISOString().slice(0, 10);
  const hasFailed = tasks.some((t) => t.status === 'failed');
  const completedCount = tasks.filter((t) => t.status === 'completed').length;
  const totalCount = tasks.length;

  return (
    <AppPage>
      <h1 className="text-xl font-semibold text-foreground mb-4">EOD 总结</h1>
      <div className="flex flex-col gap-4">

        {/* Stock list info */}
        {!loadingStocks && stockCodes.length > 0 && (
          <div className="text-sm text-secondary-text">
            持仓: {stockCodes.join(', ')} ({stockCodes.length} 只)
          </div>
        )}
        {!loadingStocks && stockCodes.length === 0 && (
          <InlineAlert variant="warning" title="未配置自选股" message="请先在系统设置中配置 STOCK_LIST。" />
        )}

        {/* Controls */}
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col gap-1.5">
            <label className="text-xs text-secondary-text font-medium">日期</label>
            <select
              value={selectedDate}
              onChange={(e) => { setSelectedDate(e.target.value); setDigest(null); setPhase('idle'); }}
              className="input min-w-[180px]"
            >
              {!availableDates.includes(selectedDate) && (
                <option value={selectedDate}>{formatDate(selectedDate)}</option>
              )}
              {availableDates.map((d) => (
                <option key={d} value={d}>{formatDate(d)}{d === new Date().toISOString().slice(0, 10) ? ' (今天)' : ''}</option>
              ))}
            </select>
          </div>

          {/* Primary action */}
          {isToday && phase === 'idle' && (
            <Button onClick={runFullWorkflow} disabled={stockCodes.length === 0} className="flex items-center gap-2">
              <Play className="h-4 w-4" />
              运行全部分析 + 生成总结
            </Button>
          )}

          {/* Generate-only for past dates */}
          {!isToday && phase === 'idle' && (
            <Button onClick={() => generateDigestOnly()} className="flex items-center gap-2">
              <RefreshCw className="h-4 w-4" />
              生成总结
            </Button>
          )}

          {/* Analyzing state */}
          {phase === 'analyzing' && (
            <div className="flex items-center gap-2">
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
              <span className="text-sm text-secondary-text">
                分析中 ({completedCount}/{totalCount})
              </span>
            </div>
          )}

          {phase === 'digesting' && (
            <div className="flex items-center gap-2">
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
              <span className="text-sm text-secondary-text">生成总结中...</span>
            </div>
          )}
        </div>

        {/* Task progress */}
        {tasks.length > 0 && (
          <div className="flex flex-col gap-1.5">
            {tasks.map((t) => (
              <div key={t.taskId} className="flex items-center gap-2 text-sm">
                {t.status === 'completed' && <CheckCircle2 className="h-4 w-4 text-green-500" />}
                {t.status === 'failed' && <XCircle className="h-4 w-4 text-red-500" />}
                {t.status === 'processing' && <Clock className="h-4 w-4 text-yellow-500 animate-pulse" />}
                <span className="font-mono">{t.stockCode}</span>
                <span className="text-secondary-text">
                  {t.status === 'completed' ? '完成' : t.status === 'failed' ? `失败: ${t.error || '未知'}` : '分析中...'}
                </span>
              </div>
            ))}
          </div>
        )}

        {/* After workflow: show digest */}
        {phase === 'done' && (
          <>
            {hasFailed && (
              <InlineAlert variant="warning" title="部分分析失败" message="部分股票分析失败，总结仅基于成功完成的股票。" />
            )}

            {digest && !digest.isToday && (
              <InlineAlert variant="warning" title="非今日数据" message={`当前显示 ${digest.targetDate} 的总结。`} />
            )}
            {digest?.anyStale && (
              <InlineAlert variant="warning" title="部分数据过时" message="部分股票分析不是最新的，已在总结中标注。" />
            )}

            {error && <ApiErrorAlert error={error} />}

            {digest?.status === 'no_data' && (
              <EmptyState icon={<Calendar className="h-12 w-12 text-muted-text" />} title="该日期无分析报告" description={digest.error || ''} />
            )}

            {digest?.status === 'ok' && digest.digestText && (
              <div className="flex flex-col gap-3">
                <div className="flex flex-wrap items-center gap-2 text-xs text-secondary-text">
                  <span className="flex items-center gap-1"><CheckCircle2 className="h-3.5 w-3.5 text-green-500" />{digest.stockCount} 只股票</span>
                  {digest.modelUsed && <span>· {digest.modelUsed}</span>}
                  <span>· {digest.targetDate}</span>
                </div>
                {digest.stocksFreshness && <FreshnessBadges freshness={digest.stocksFreshness} />}
                <div className="card p-4">
                  <div className="prose prose-sm max-w-none dark:prose-invert whitespace-pre-wrap text-sm leading-relaxed">
                    {digest.digestText}
                  </div>
                </div>
              </div>
            )}

            {digest?.status === 'error' && (
              <InlineAlert variant="danger" title="生成失败" message={digest.error || '请重试。'} />
            )}

            {/* Re-run button */}
            <div className="flex gap-3">
              {isToday && (
                <Button onClick={runFullWorkflow} variant="outline" className="flex items-center gap-2">
                  <RefreshCw className="h-4 w-4" /> 重新运行
                </Button>
              )}
              {!isToday && (
                <Button onClick={() => generateDigestOnly()} variant="outline" className="flex items-center gap-2">
                  <RefreshCw className="h-4 w-4" /> 重新生成
                </Button>
              )}
            </div>
          </>
        )}

        {/* Idle state (no digest yet) */}
        {phase === 'idle' && (
          <EmptyState
            icon={<FileText className="h-12 w-12 text-muted-text" />}
            title={isToday ? '一键运行 EOD 分析' : '查看历史总结'}
            description={
              isToday
                ? '点击上方按钮，系统将自动运行所有持仓的个股分析，完成后生成投资组合级别总结。'
                : '选择过去日期后点击「生成总结」，系统将读取该日期的分析报告生成概述。'
            }
          />
        )}
      </div>
    </AppPage>
  );
};

export default EodSummaryPage;
