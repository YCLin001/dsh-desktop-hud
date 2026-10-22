window.__ModuleLoader__.load({
  id: "dsh-client-ui-hud",
  factory: (require) => {
    var module = { exports: {} };
    var exports = module.exports;
    Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });

    /* ------------------------------------------------------------------ *
     * Desktop overlay bridge
     *
     * The desktop window (overlay/hud_overlay.py) is independent of the DSH
     * window. This browser half only publishes the current Session snapshot
     * to its localhost bridge and executes the commands it queues.
     * ------------------------------------------------------------------ */

    const OVERLAY_URL = "http://127.0.0.1:38471";
    const EMPTY_SESSION = Object.freeze({ running: false, openState: "cold" });
    const EMPTY_CHAT = Object.freeze({ order: [], nodes: null, timeline: null });

    function oneLine(text, max = 120) {
      const value = String(text ?? "").replace(/\s+/g, " ").trim();
      return value.length > max ? value.slice(0, max - 1) + "…" : value;
    }

    function contentText(content) {
      if (typeof content === "string") return content;
      if (!Array.isArray(content)) return "";
      const parts = [];
      for (const block of content) {
        if (!block || typeof block !== "object") continue;
        if (block.type === "text" && typeof block.text === "string") parts.push(block.text);
        else if (block.type === "image") parts.push("[图片]");
        else if (block.type === "file") parts.push("[文件]");
      }
      return parts.join("\n");
    }

    function parseArgs(raw) {
      if (typeof raw !== "string" || raw.trim() === "") return {};
      try {
        const value = JSON.parse(raw);
        return value && typeof value === "object" && !Array.isArray(value) ? value : {};
      } catch {
        return {};
      }
    }

    function summarize(value, max = 90) {
      if (value === undefined || value === null) return "";
      const text = typeof value === "string" ? value : JSON.stringify(value);
      return oneLine(text, max);
    }

    const ARG_KEYS = ["command", "file_path", "path", "pattern", "query", "url", "description", "prompt", "objective", "label", "name"];

    function argsSummary(raw) {
      const args = parseArgs(raw);
      for (const key of ARG_KEYS) {
        if (args[key] !== undefined && args[key] !== null && String(args[key]).trim() !== "") {
          return summarize(args[key]);
        }
      }
      for (const value of Object.values(args)) {
        if (typeof value === "string" && value.trim() !== "") return summarize(value);
      }
      const fallback = String(raw ?? "").replace(/^[\s{[]+|[\s}\]]+$/g, "");
      return oneLine(fallback, 90);
    }

    const TOOL_LABELS = {
      pwsh: "运行命令",
      bash: "运行命令",
      read: "读取文件",
      write: "写入文件",
      edit: "修改文件",
      glob: "查找文件",
      grep: "搜索内容",
      web_search: "搜索网页",
      web_fetch: "读取网页",
      subagent: "委派子任务",
      subagent_fork: "委派子任务",
      todo_write: "更新计划",
      present: "展示成果",
      ask_user_question: "向你确认",
      skill: "加载技能",
      create_goal: "创建目标",
      update_goal: "更新目标",
      workflow: "编排工作流",
      jobs: "管理后台任务"
    };

    function toolLabel(name) {
      if (!name) return "工具调用";
      return TOOL_LABELS[name] ?? name;
    }

    function turnOf(location) {
      const turn = location?.turn;
      return typeof turn === "number" ? turn : turn?.turn;
    }

    function stepOf(location) {
      const step = location?.step;
      return typeof step === "number" ? step : step?.step;
    }

    function stepLabel(node) {
      const location = node?.location;
      const turn = turnOf(location);
      const step = stepOf(location);
      if (turn === undefined || turn === null) return "";
      return step === undefined || step === null ? `第 ${turn} 轮` : `第 ${turn} 轮 · 第 ${step} 步`;
    }

    function assistantText(data) {
      const blocks = Array.isArray(data?.blocks) ? data.blocks : [];
      let text = "";
      let reasoning = "";
      for (const block of blocks) {
        if (!block || typeof block !== "object") continue;
        if (block.kind === "text" && typeof block.text === "string" && block.text.trim() !== "") text = block.text;
        if (block.kind === "reasoning" && typeof block.text === "string" && block.text.trim() !== "") reasoning = block.text;
      }
      return { text, reasoning };
    }

    function formatArgs(raw) {
      const args = parseArgs(raw);
      if (Object.keys(args).length > 0) {
        try {
          return JSON.stringify(args, null, 2);
        } catch {
          return String(raw ?? "");
        }
      }
      return typeof raw === "string" ? raw : "";
    }

    function buildItems(snapshot) {
      if (!snapshot || !Array.isArray(snapshot.order)) return [];
      const store = snapshot.nodes;
      if (!store || typeof store.get !== "function") return [];
      const items = [];
      for (const key of snapshot.order) {
        const node = store.get(key);
        if (!node || node.visibility === "hidden") continue;
        const data = node.data;
        if (node.kind === "tool-call") {
          const root = data?.root;
          if (!root) continue;
          if (root.kind === "tool-result") {
            const result = contentText(root.content);
            const name = root.name || root.call?.name || "";
            items.push({
              key: node.key,
              node,
              kind: "tool",
              live: false,
              status: root.isError ? "error" : "done",
              name,
              title: root.isError ? `${toolLabel(name)}失败` : toolLabel(name),
              subtitle: oneLine(result || (root.isError ? "执行失败" : "已完成"), 120),
              time: root.time ?? node.data?.time,
              detail: () => {
                const args = root.argsRaw || root.call?.argsRaw || "";
                const pretty = formatArgs(args);
                return `工具：${name || "(unknown)"}\n状态：${root.isError ? "失败" : "完成"}\n\n参数：\n${pretty || "(无)"}\n\n结果：\n${oneLine(result, 6000) || "(无文本结果)"}`;
              }
            });
          } else {
            const name = root.name || "";
            const summary = argsSummary(root.argsRaw);
            items.push({
              key: node.key,
              node,
              kind: "tool",
              live: true,
              status: "running",
              name,
              title: toolLabel(name),
              subtitle: summary || (root.phase === "preparing" ? "准备参数…" : "执行中…"),
              time: root.time,
              detail: () => `工具：${name || "(unknown)"}\n状态：执行中\n\n参数：\n${formatArgs(root.argsRaw) || "(参数尚未完整)"}`
            });
          }
          continue;
        }
        if (node.kind === "assistant-step") {
          const { text, reasoning } = assistantText(data);
          const live = data?.status === "running";
          const preview = oneLine(text || reasoning || "", 120);
          items.push({
            key: node.key,
            node,
            kind: "assistant",
            live,
            status: data?.status === "interrupted" ? "error" : live ? "running" : "done",
            title: live ? (text ? "正在输出" : "DeepSeek 思考中") : "DeepSeek",
            subtitle: preview || (live ? "等待模型响应…" : ""),
            time: data?.time,
            detail: () => {
              const blocks = Array.isArray(data?.blocks) ? data.blocks : [];
              const body = blocks.map((block) => {
                if (!block || typeof block !== "object") return "";
                if (block.kind === "reasoning") return `[思考]\n${block.text ?? ""}`;
                if (block.kind === "text") return block.text ?? "";
                if (block.kind === "tool-call") return `[调用工具 ${block.name ?? ""}]`;
                return "";
              }).filter(Boolean).join("\n\n");
              return body ? oneLine(body, 6000) : "(尚无内容)";
            }
          });
          continue;
        }
        if (node.kind === "command") {
          const name = data?.name ?? data?.command?.name ?? "";
          items.push({
            key: node.key,
            node,
            kind: "command",
            live: false,
            status: "done",
            title: `/${name || "命令"}`,
            subtitle: "命令",
            time: data?.time,
            detail: () => oneLine(JSON.stringify(data, null, 2), 4000)
          });
          continue;
        }
        if (node.kind === "turn-error") {
          const message = data?.error?.message ?? data?.message ?? "本轮执行失败";
          items.push({
            key: node.key,
            node,
            kind: "error",
            live: false,
            status: "error",
            title: "执行失败",
            subtitle: oneLine(message, 140),
            time: data?.time ?? node.data?.time,
            detail: () => oneLine(JSON.stringify(data, null, 2), 6000)
          });
          continue;
        }
        if (node.kind === "compaction" || node.kind === "manual-compaction") {
          items.push({
            key: node.key,
            node,
            kind: "compaction",
            live: false,
            status: "done",
            title: node.kind === "compaction" ? "自动压缩上下文" : "压缩上下文",
            subtitle: "历史已整理",
            time: data?.time,
            detail: () => oneLine(JSON.stringify(data, null, 2), 4000)
          });
        }
      }
      return items;
    }

    function startOverlayBridge(ctx) {
      let disposed = false;
      let currentBinding = null;
      let unsubCurrent = null;
      let unsubSession = null;
      let unsubChat = null;
      let pending = null;
      let lastSent = 0;
      let pollTimer = null;
      let heartbeatTimer = null;
      let polling = false;

      const postStatus = (payload) => {
        try {
          fetch(OVERLAY_URL + "/status", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
            cache: "no-store"
          }).catch(() => {});
        } catch {
          /* overlay is optional */
        }
      };

      const snapshot = () => {
        const binding = ctx.uiSession.current.getSnapshot();
        if (!binding || binding.key === undefined || binding.key === null) {
          return { connected: false, running: false, title: "", step: "", current: null, items: [], updatedAt: Date.now() };
        }
        const session = binding.hooks?.session;
        const chat = binding.hooks?.chat;
        const sessionSnapshot = session?.getSnapshot?.() ?? EMPTY_SESSION;
        const chatSnapshot = chat?.getSnapshot?.() ?? EMPTY_CHAT;
        const list = ctx.sessions.list.getSnapshot();
        const all = buildItems(chatSnapshot).slice(-30);
        const current = all.length > 0 ? all[all.length - 1] : null;
        const detailFrom = Math.max(0, all.length - 12);
        return {
          connected: true,
          sessionId: binding.key,
          title: list?.byId?.[binding.key]?.displayTitle ?? String(binding.key).slice(0, 8),
          running: sessionSnapshot.running === true,
          step: current ? stepLabel(current.node) : "",
          current: current ? { title: current.title, subtitle: current.subtitle, status: current.status } : null,
          error: sessionSnapshot.promptError?.error?.message ?? sessionSnapshot.lastAgentError ?? null,
          items: all.map((item, index) => {
            let detail = "";
            if (index >= detailFrom && typeof item.detail === "function") {
              try {
                detail = item.detail().slice(0, 1800);
              } catch {
                detail = "";
              }
            }
            return {
              key: item.key,
              kind: item.kind,
              title: item.title,
              subtitle: item.subtitle,
              time: item.time,
              status: item.status,
              live: item.live === true,
              detail
            };
          }),
          updatedAt: Date.now()
        };
      };

      const flush = () => {
        if (disposed) return;
        const now = Date.now();
        if (now - lastSent < 450) {
          if (pending === null) {
            pending = setTimeout(() => {
              pending = null;
              flush();
            }, 450 - (now - lastSent));
          }
          return;
        }
        lastSent = now;
        postStatus(snapshot());
      };

      const attach = () => {
        const binding = ctx.uiSession.current.getSnapshot();
        if (binding === currentBinding) {
          flush();
          return;
        }
        currentBinding = binding;
        if (typeof unsubSession === "function") unsubSession();
        if (typeof unsubChat === "function") unsubChat();
        unsubSession = null;
        unsubChat = null;
        const session = binding?.hooks?.session;
        const chat = binding?.hooks?.chat;
        if (session && typeof session.subscribe === "function") unsubSession = session.subscribe(flush);
        if (chat && typeof chat.subscribe === "function") unsubChat = chat.subscribe(flush);
        flush();
      };

      const runCommand = async (command) => {
        if (!command || !command.type) return;
        const binding = ctx.uiSession.current.getSnapshot();
        const session = binding?.hooks?.session;
        if (!session) return;
        try {
          if (command.type === "stop" && typeof session.cancel === "function") {
            await session.cancel();
          } else if (command.type === "send" && typeof session.prompt === "function"
            && typeof command.text === "string" && command.text.trim() !== "") {
            await session.prompt([{ type: "text", text: command.text }],
              command.mode === "steer" ? "steer" : "queue");
          } else if (command.type === "new-session"
            && ctx.uiWorkspace && typeof ctx.uiWorkspace.startSession === "function") {
            await ctx.uiWorkspace.startSession();
          }
        } catch {
          /* the desktop window does not own error presentation */
        }
      };

      const poll = async () => {
        if (disposed) return;
        if (!polling) {
          polling = true;
          try {
            const response = await fetch(OVERLAY_URL + "/command", { cache: "no-store" });
            if (response.ok) {
              const command = await response.json();
              await runCommand(command);
            }
          } catch {
            /* overlay not running */
          } finally {
            polling = false;
          }
        }
        pollTimer = setTimeout(poll, 600);
      };

      unsubCurrent = typeof ctx.uiSession.current.subscribe === "function"
        ? ctx.uiSession.current.subscribe(attach)
        : null;
      attach();
      pollTimer = setTimeout(poll, 600);
      heartbeatTimer = setInterval(flush, 2000);

      return () => {
        disposed = true;
        if (typeof unsubCurrent === "function") unsubCurrent();
        if (typeof unsubSession === "function") unsubSession();
        if (typeof unsubChat === "function") unsubChat();
        if (pending !== null) clearTimeout(pending);
        if (pollTimer !== null) clearTimeout(pollTimer);
        if (heartbeatTimer !== null) clearInterval(heartbeatTimer);
      };
    }

    function apply(ctx) {
      ctx.effect(() => startOverlayBridge(ctx), "floating-hud: desktop overlay bridge");
    }

    exports.apply = apply;
    exports.inject = ["sessions", "uiSession", "uiWorkspace"];
    return module.exports;
  }
});
