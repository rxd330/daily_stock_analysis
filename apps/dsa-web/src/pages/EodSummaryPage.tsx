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
  const [availableDates, setAvailableDates] = useState<string[]>([]);
  const [selectedDate, setSelectedDate] = useState<string>(() => new Date().toISOString().slice(0, 10));
  const [stockCodes, setStockCodes] = useState<string[]>([]);
  const [loadingStocks, setLoadingStocks] = useState(true);
  const [hasReportsToday, setHasReportsToday] = useState<boolean | null>(null);

  const [phase, setPhase] = useState<'idle' | 'analyzing' | 'digesting' | 'done'>('idle');
  const [digestStep, setDigestStep] = useState<1 | 2 | 3 | 4>(1);
  const [tasks, setTasks] = useState<TaskState[]>([]);
  const [digest, setDigest] = useState<PortfolioDigestResponse | null>(null);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const pollTimer = useRef<number | null>(null);

  // ---- Load stock list ----
  const loadStockList = useCallback(async () => {
    setLoadingStocks(true);
    try {
      const result = await watchlistApi.list();
      setStockCodes(result.items.map((i) => i.code));
    } catch {
      try {
        const config = await systemConfigApi.getConfig(false);
        const item = config.items?.find((i) => i.key === 'STOCK_LIST');
        if (item?.value) setStockCodes(item.value.split(',').map((c) => c.trim()).filter(Boolean));
      } catch { /* silent */ }
    } finally {
      setLoadingStocks(false);
    }
  }, []);

  // ---- Load available dates & check today ----
  const loadDates = useCallback(async () => {
    try {
      const result = await portfolioDigestApi.getAvailableDates();
      setAvailableDates(result.dates);
      const today = new Date().toISOString().slice(0, 10);
      setHasReportsToday(result.dates.includes(today));
    } catch { /* silent */ }
  }, []);

  useEffect(() => {
    void loadStockList();
    void loadDates();
  }, [loadStockList, loadDates]);

  useEffect(() => {
    return () => { if (pollTimer.current !== null) window.clearInterval(pollTimer.current); };
  }, []);

  // ---- Poll tasks ----
  const pollTasks = useCallback(async (taskList: TaskState[]) => {
    const updated = await Promise.all(taskList.map(async (t) => {
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
    }));
    return updated;
  }, []);

  // ---- Generate digest only (fast — reads existing reports) ----
  const generateDigestOnly = useCallback(async (date?: string) => {
    setPhase('digesting');
    setDigestStep(1);
    setError(null);

    // Step 1→2: reading reports from DB
    await new Promise((r) => setTimeout(r, 600));
    setDigestStep(2);

    // Step 2→3: simulate freshness check (instant in backend)
    await new Promise((r) => setTimeout(r, 400));
    setDigestStep(3);

    try {
      const result = await portfolioDigestApi.generate({ date: date || selectedDate, lang: 'zh' });
      setDigestStep(4);
      setDigest(result);
      // Brief pause so user sees step 4 complete before result replaces progress
      await new Promise((r) => setTimeout(r, 600));
      setPhase('done');
      void loadDates();
    } catch (err) {
      setDigestStep(4);
      setError(getParsedApiError(err));
      await new Promise((r) => setTimeout(r, 800));
      setPhase('idle');
    }
  }, [selectedDate, loadDates]);

  // ---- Run full analysis then digest (secondary workflow) ----
  const runFullWorkflow = useCallback(async () => {
    if (stockCodes.length === 0) return;
    setPhase('analyzing');
    setError(null);
    setDigest(null);
    try {
      const response = await analysisApi.analyzeAsync({ stockCodes, reportType: 'full', asyncMode: true, reportLanguage: 'zh' });
      let taskList: TaskState[] = [];
      if ('accepted' in response && Array.isArray(response.accepted)) {
        taskList = response.accepted.map((item) => ({ stockCode: item.stockCode, taskId: item.taskId, status: 'processing' as const }));
      } else if ('taskId' in response) {
        taskList = [{ stockCode: stockCodes[0], taskId: (response as { taskId: string }).taskId, status: 'processing' as const }];
      }
      setTasks(taskList);
      await new Promise<void>((resolve) => {
        const interval = window.setInterval(async () => {
          const updated = await pollTasks(taskList);
          setTasks(updated);
          taskList = updated;
          if (updated.every((t) => t.status === 'completed' || t.status === 'failed')) {
            window.clearInterval(interval);
            resolve();
          }
        }, 3000);
        pollTimer.current = interval;
      });
      setPhase('digesting');
      const result = await portfolioDigestApi.generate({ date: selectedDate, lang: 'zh' });
      setDigest(result);
      setPhase('done');
      void loadDates();
    } catch (err) {
      setError(getParsedApiError(err));
      setPhase('idle');
    }
  }, [stockCodes, selectedDate, pollTasks, loadDates]);

  const formatDate = (dateStr: string) => {
    try {
      const d = new Date(dateStr + 'T00:00:00');
      return d.toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'short' });
    } catch { return dateStr; }
  };

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

        {/* Watchlist info */}
        {!loadingStocks && stockCodes.length > 0 && (
          <div className="text-sm text-secondary-text">
            持仓: {stockCodes.length} 只 ({stockCodes.slice(0, 6).join(', ')}{stockCodes.length > 6 ? '...' : ''})
          </div>
        )}
        {!loadingStocks && stockCodes.length === 0 && (
          <InlineAlert variant="warning" title="未配置持仓" message="请先在系统设置或首页添加自选股。" />
        )}

        {/* Controls */}
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col gap-1.5">
            <label className="text-xs text-secondary-text font-medium">日期</label>
            <select
              value={selectedDate}
              onChange={(e) => { setSelectedDate(e.target.value); setDigest(null); setPhase('idle'); setTasks([]); }}
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

          {/* Primary: Generate summary from existing reports */}
          {phase === 'idle' && (
            <Button onClick={() => generateDigestOnly()} className="flex items-center gap-2">
              <RefreshCw className="h-4 w-4" />
              生成总结
            </Button>
          )}

          {/* Secondary: Run analysis first (only for today, only if no reports yet) */}
          {phase === 'idle' && isToday && hasReportsToday === false && stockCodes.length > 0 && (
            <Button onClick={runFullWorkflow} variant="outline" className="flex items-center gap-2">
              <Play className="h-4 w-4" />
              先运行分析
            </Button>
          )}

          {/* States */}
          {phase === 'analyzing' && (
            <div className="flex items-center gap-2">
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
              <span className="text-sm text-secondary-text">分析中 ({completedCount}/{totalCount})</span>
            </div>
          )}
          {phase === 'digesting' && (
            <div className="flex flex-col gap-2 mt-2 w-full max-w-md">
              {[
                { step: 1, label: '读取分析报告', desc: '从数据库加载今日个股分析结果' },
                { step: 2, label: '检查数据新鲜度', desc: '标记每只股票的报告日期' },
                { step: 3, label: 'AI 生成总结', desc: '调用大模型生成投资组合概述' },
                { step: 4, label: '完成', desc: '总结已生成' },
              ].map((s) => {
                const isActive = s.step === digestStep;
                const isDone = s.step < digestStep;
                const isPending = s.step > digestStep;
                return (
                  <div key={s.step} className={`flex items-start gap-3 transition-opacity ${isPending ? 'opacity-30' : 'opacity-100'}`}>
                    <div className={`flex-shrink-0 w-5 h-5 mt-0.5 rounded-full flex items-center justify-center text-xs font-bold
                      ${isDone ? 'bg-green-500 text-white' : isActive ? 'bg-primary text-white animate-pulse' : 'bg-border text-secondary-text'}`}>
                      {isDone ? <CheckCircle2 className="h-3.5 w-3.5" /> : s.step}
                    </div>
                    <div>
                      <div className={`text-sm font-medium ${isDone ? 'text-green-600' : isActive ? 'text-foreground' : 'text-secondary-text'}`}>
                        {s.label}
                      </div>
                      {isActive && <div className="text-xs text-secondary-text mt-0.5">{s.desc}</div>}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* No reports today hint */}
        {phase === 'idle' && isToday && hasReportsToday === false && (
          <InlineAlert
            variant="info"
            title="今日尚无分析报告"
            message="点击「先运行分析」对全部持仓运行个股分析，完成后会自动生成总结。如果你已在首页运行过分析，直接点「生成总结」即可。"
          />
        )}

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

        {/* Digest result */}
        {phase === 'done' && (
          <>
            {hasFailed && <InlineAlert variant="warning" title="部分分析失败" message="总结仅基于成功完成的股票。" />}
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
            <div className="flex gap-3">
              <Button onClick={() => generateDigestOnly()} variant="outline" className="flex items-center gap-2">
                <RefreshCw className="h-4 w-4" /> 重新生成
              </Button>
            </div>
          </>
        )}

        {/* Idle */}
        {phase === 'idle' && (
          <EmptyState
            icon={<FileText className="h-12 w-12 text-muted-text" />}
            title={isToday ? '生成今日持仓总结' : '查看历史总结'}
            description={
              isToday
                ? '点击「生成总结」，系统将读取今日已有的个股分析报告，用 LLM 生成投资组合级别概述。'
                : '选择日期后点击「生成总结」。'
            }
          />
        )}
      </div>
    </AppPage>
  );
};

export default EodSummaryPage;
