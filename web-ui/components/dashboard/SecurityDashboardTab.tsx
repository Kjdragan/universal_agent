"use client";

import React, { useEffect, useState } from "react";

export function SecurityDashboardTab() {
  const [report, setReport] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch("/security_report.json")
      .then((res) => {
        if (!res.ok) throw new Error("Report not found");
        return res.json();
      })
      .then((data) => {
        // Handle Promptfoo output structure
        if (data && data.results && data.results.results) {
         setReport(data.results);
        } else {
         setReport(data);
        }
      })
      .catch((err) => {
        console.error("Failed to fetch security report:", err);
        setReport({ error: "No nightly security report found. Run the cron job manually to generate one." });
      })
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center p-8 text-muted-foreground">
        <div className="animate-spin w-8 h-8 border-2 border-primary border-t-transparent rounded-full mb-4"></div>
        <p>Loading Latest Red Team Evaluation...</p>
      </div>
    );
  }

  if (report?.error) {
    return (
      <div className="p-6 text-center text-muted-foreground">
        <span className="text-3xl mb-2 block">🛡️</span>
        <h2 className="text-lg text-foreground mb-2">No Security Data Available</h2>
        <p className="text-sm">{report.error}</p>
      </div>
    );
  }

  const results = report?.results || [];
  const stats = report?.stats || { successes: 0, failures: 0 };
  const totalTests = stats.successes + stats.failures || results.length;
  const passRate = totalTests > 0 ? Math.round((stats.successes / totalTests) * 100) : 0;

  return (
    <div className="flex flex-col h-full bg-background text-foreground/80 p-6 overflow-y-auto">
      <div className="flex items-center justify-between mb-8 border-b border-border pb-4">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-3">
            <span className="text-primary">🛡️</span> 
            Universal Agent Security Operations
          </h1>
          <p className="text-sm text-muted-foreground mt-1 uppercase tracking-widest font-mono">
            Nightly Red Team Evaluation Dashboard
          </p>
        </div>
        <div className="flex gap-4">
          <div className="bg-background border border-border rounded-lg p-3 text-center min-w-[120px]">
            <p className="text-[10px] text-muted-foreground uppercase tracking-widest mb-1">Pass Rate</p>
            <p className={`text-2xl font-mono ${passRate >= 90 ? 'text-primary' : passRate >= 70 ? 'text-accent' : 'text-red-500'}`}>
              {passRate}%
            </p>
          </div>
          <div className="bg-background border border-border rounded-lg p-3 text-center min-w-[120px]">
            <p className="text-[10px] text-muted-foreground uppercase tracking-widest mb-1">Total Tests</p>
            <p className="text-2xl font-mono text-primary">{totalTests}</p>
          </div>
        </div>
      </div>

      <div className="gap-6 grid grid-cols-1">
        <div className="bg-background/50 border border-border rounded-xl overflow-hidden">
          <div className="bg-card/50 p-3 border-b border-border">
            <h2 className="text-sm font-bold uppercase tracking-widest text-foreground/80">Detailed Findings</h2>
          </div>
          <div className="divide-y divide-slate-800/50">
            {results.map((res: any, idx: number) => {
              const isVuln = res.success === false || (res.gradingResult && !res.gradingResult.pass);
              const label = res.prompt?.label || "Adversarial Prompt";
              return (
                <div key={idx} className="p-4 flex flex-col gap-2">
                  <div className="flex justify-between items-start">
                     <span className="font-mono text-sm text-primary max-w-[80%] break-words">
                       {res.prompt?.raw || String(res.prompt)}
                     </span>
                     <span className={`text-[10px] font-bold px-2 py-1 rounded-full uppercase tracking-widest shrink-0 ${isVuln ? 'bg-red-500/20 border border-red-500/50 text-red-500' : 'bg-primary/20 border border-primary/30 text-primary'}`}>
                       {isVuln ? 'VULNERABILITY DETECTED' : 'BLOCKED'}
                     </span>
                  </div>
                  
                  <div className="text-xs text-muted-foreground bg-black/20 p-2 rounded whitespace-pre-wrap font-mono relative mt-1">
                    <div className="absolute top-0 right-0 text-[9px] bg-card px-2 py-1 -mt-2 -mr-2 rounded opacity-50 uppercase shadow-md">Agent Output</div>
                    {typeof res.response === 'string' ? res.response : res.response?.output || JSON.stringify(res.response)}
                  </div>
                  
                  {isVuln && res.gradingResult?.reason && (
                    <div className="text-xs text-red-400 bg-red-950/30 p-2 rounded mt-1 font-mono border border-red-900/50">
                      <strong>Grader Note:</strong> {res.gradingResult.reason}
                    </div>
                  )}
                </div>
              );
            })}
            
            {results.length === 0 && (
               <div className="p-8 text-center text-muted-foreground">No evaluation results in the payload.</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
