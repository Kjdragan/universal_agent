"use client";

import { useEffect, useState, useRef, useSyncExternalStore } from "react";
import { DragDropContext, Droppable, Draggable, DropResult } from "@hello-pangea/dnd";
import { CopyPlus, Clock, Zap, User, Trash2, Pencil, X } from "lucide-react";

type ColumnType = "Backlog" | "In Progress" | "In Review" | "Done";

interface Task {
  id: string;
  title: string;
  description: string;
  status: ColumnType;
  priority: "High" | "Medium" | "Low";
  assignee: string;
  points: number;
}

const INITIAL_TASKS: Task[] = [];

const COLUMNS: ColumnType[] = ["Backlog", "In Progress", "In Review", "Done"];
const PRIORITIES: Task["priority"][] = ["High", "Medium", "Low"];

/* ── Edit Modal ─────────────────────────────────────────────────────── */

function EditModal({
  task,
  onSave,
  onClose,
}: {
  task: Task;
  onSave: (updated: Task) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState<Task>({ ...task });
  const titleRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    titleRef.current?.focus();
    titleRef.current?.select();
  }, []);

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!draft.title.trim()) return;
    onSave(draft);
  };

  return (
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <form
        onClick={(e) => e.stopPropagation()}
        onSubmit={handleSubmit}
        className="w-full max-w-lg bg-[#0c1528] border border-white/10 shadow-2xl shadow-black/50 flex flex-col animate-in fade-in zoom-in-95 duration-150"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-white/10">
          <div className="flex items-center gap-2">
            <span className="font-mono text-[11px] text-cyan-500 font-bold bg-cyan-500/10 px-1.5 py-0.5">
              {task.id}
            </span>
            <span className="font-mono text-[10px] text-slate-400 uppercase tracking-widest">
              Edit Task
            </span>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1 text-slate-400 hover:text-white hover:bg-white/10 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-4 flex flex-col gap-4">
          {/* Title */}
          <label className="flex flex-col gap-1.5">
            <span className="font-mono text-[10px] text-slate-400 uppercase tracking-widest">
              Title
            </span>
            <input
              ref={titleRef}
              type="text"
              value={draft.title}
              onChange={(e) => setDraft({ ...draft, title: e.target.value })}
              className="bg-white/5 border border-white/10 px-3 py-2 text-sm text-white outline-none focus:border-cyan-500/50 transition-colors placeholder:text-slate-600"
              placeholder="Task title…"
            />
          </label>

          {/* Description */}
          <label className="flex flex-col gap-1.5">
            <span className="font-mono text-[10px] text-slate-400 uppercase tracking-widest">
              Description
            </span>
            <textarea
              value={draft.description}
              onChange={(e) => setDraft({ ...draft, description: e.target.value })}
              rows={3}
              className="bg-white/5 border border-white/10 px-3 py-2 text-sm text-white outline-none focus:border-cyan-500/50 transition-colors resize-y placeholder:text-slate-600"
              placeholder="Mission details…"
            />
          </label>

          {/* Row: Priority / Status */}
          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-1.5">
              <span className="font-mono text-[10px] text-slate-400 uppercase tracking-widest">
                Priority
              </span>
              <select
                value={draft.priority}
                onChange={(e) =>
                  setDraft({ ...draft, priority: e.target.value as Task["priority"] })
                }
                className="bg-white/5 border border-white/10 px-3 py-2 text-sm text-white outline-none focus:border-cyan-500/50 transition-colors appearance-none cursor-pointer"
              >
                {PRIORITIES.map((p) => (
                  <option key={p} value={p} className="bg-[#0c1528]">
                    {p}
                  </option>
                ))}
              </select>
            </label>

            <label className="flex flex-col gap-1.5">
              <span className="font-mono text-[10px] text-slate-400 uppercase tracking-widest">
                Status
              </span>
              <select
                value={draft.status}
                onChange={(e) =>
                  setDraft({ ...draft, status: e.target.value as ColumnType })
                }
                className="bg-white/5 border border-white/10 px-3 py-2 text-sm text-white outline-none focus:border-cyan-500/50 transition-colors appearance-none cursor-pointer"
              >
                {COLUMNS.map((c) => (
                  <option key={c} value={c} className="bg-[#0c1528]">
                    {c}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {/* Row: Assignee / Points */}
          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-1.5">
              <span className="font-mono text-[10px] text-slate-400 uppercase tracking-widest">
                Assignee
              </span>
              <input
                type="text"
                value={draft.assignee}
                onChange={(e) => setDraft({ ...draft, assignee: e.target.value })}
                className="bg-white/5 border border-white/10 px-3 py-2 text-sm text-white outline-none focus:border-cyan-500/50 transition-colors placeholder:text-slate-600"
                placeholder="Agent name…"
              />
            </label>

            <label className="flex flex-col gap-1.5">
              <span className="font-mono text-[10px] text-slate-400 uppercase tracking-widest">
                Points
              </span>
              <input
                type="number"
                min={0}
                max={99}
                value={draft.points}
                onChange={(e) =>
                  setDraft({ ...draft, points: Math.max(0, parseInt(e.target.value) || 0) })
                }
                className="bg-white/5 border border-white/10 px-3 py-2 text-sm text-white outline-none focus:border-cyan-500/50 transition-colors"
              />
            </label>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-white/10">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 font-mono text-[11px] font-semibold text-slate-400 uppercase tracking-widest hover:text-white hover:bg-white/5 transition-colors"
          >
            Cancel
          </button>
          <button
            type="submit"
            className="px-5 py-2 bg-cyan-400 font-mono text-[11px] font-bold text-black uppercase tracking-widest hover:bg-cyan-300 active:scale-95 transition-all"
          >
            Save Changes
          </button>
        </div>
      </form>
    </div>
  );
}

/* ── Board ──────────────────────────────────────────────────────────── */

const STORAGE_KEY = "ua.kanban_tasks.v1";
const subscribeToHydration = () => () => {};
const clientHydratedSnapshot = () => true;
const serverHydratedSnapshot = () => false;

export default function KanbanBoard() {
  const [tasks, setTasks] = useState<Task[]>(() => {
    if (typeof window === "undefined") return INITIAL_TASKS;
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) {
        const parsed = JSON.parse(stored) as Task[];
        if (Array.isArray(parsed) && parsed.length > 0) return parsed;
      }
    } catch { /* ignore corrupt data */ }
    return INITIAL_TASKS;
  });
  const isMounted = useSyncExternalStore(
    subscribeToHydration,
    clientHydratedSnapshot,
    serverHydratedSnapshot,
  );
  const [editingTask, setEditingTask] = useState<Task | null>(null);

  // Persist tasks to localStorage on every change
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(tasks));
    } catch { /* storage full or unavailable */ }
  }, [tasks]);

  const handleNewTask = () => {
    const newTask: Task = {
      id: `TASK-${Math.floor(Math.random() * 900) + 100}`,
      title: "New Objective",
      description: "Awaiting mission details...",
      status: "Backlog",
      priority: "Medium",
      assignee: "Unassigned",
      points: 1,
    };
    setTasks([newTask, ...tasks]);
    // Auto-open the edit modal for new tasks so users can fill in details
    setEditingTask(newTask);
  };

  const handleDeleteTask = (taskId: string) => {
    setTasks(tasks.filter((t) => t.id !== taskId));
  };

  const handleSaveTask = (updated: Task) => {
    setTasks((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
    setEditingTask(null);
  };

  const onDragEnd = (result: DropResult) => {
    if (!result.destination) return;
    const { source, destination, draggableId } = result;

    if (source.droppableId === destination.droppableId && source.index === destination.index) {
      return;
    }

    const draggedTask = tasks.find((t) => t.id === draggableId);
    if (!draggedTask) return;

    const destStatus = destination.droppableId as ColumnType;

    // Filter out the dragged task
    const filteredTasks = tasks.filter((t) => t.id !== draggableId);

    // Filter tasks by destination status to determine the insertion index context
    const destTasks = filteredTasks.filter((t) => t.status === destStatus);
    
    // Create the updated task
    const updatedTask = { ...draggedTask, status: destStatus };
    
    // Insert updated task into the specific position of the dest status list
    destTasks.splice(destination.index, 0, updatedTask);

    // Reconstruct the full list, maintaining order of other columns
    const finalTasks = [
      ...filteredTasks.filter((t) => t.status !== destStatus),
      ...destTasks,
    ];

    setTasks(finalTasks);
  };

  if (!isMounted) return null;

  return (
    <div className="w-full h-full flex flex-col">
      {/* Edit Modal */}
      {editingTask && (
        <EditModal
          task={editingTask}
          onSave={handleSaveTask}
          onClose={() => setEditingTask(null)}
        />
      )}

      {/* Tactical Sub-Header */}
      <div className="flex items-center justify-between mb-6 pb-2 border-b border-white/10">
        <div>
          <h2 className="text-xl font-extrabold tracking-tighter text-white">KANBAN MATRIX</h2>
          <p className="font-mono text-[11px] text-cyan-500 uppercase tracking-widest mt-1">
            System Operation Pipelines
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button 
            onClick={handleNewTask}
            className="rounded-none bg-cyan-400 px-4 py-2 text-[13px] font-semibold text-black transition-all hover:bg-cyan-300 active:scale-95 flex items-center gap-2"
          >
            <CopyPlus className="w-4 h-4" />
            NEW TASK
          </button>
        </div>
      </div>

      <div className="flex-1 flex gap-4 overflow-x-auto pb-4">
        <DragDropContext onDragEnd={onDragEnd}>
          {COLUMNS.map((columnId) => {
            const columnTasks = tasks.filter((t) => t.status === columnId);
            return (
              <div key={columnId} className="flex-shrink-0 w-[340px] flex flex-col">
                {/* Column Header */}
                <div className="flex items-center justify-between px-3 py-2 bg-[#0b1326] border-t border-x border-white/10">
                  <span className="font-mono text-sm uppercase font-semibold text-slate-200">
                    {columnId}
                  </span>
                  <span className="font-mono text-[11px] bg-white/5 text-cyan-400 px-2 py-0.5">
                    {columnTasks.length} OP{columnTasks.length !== 1 ? 'S' : ''}
                  </span>
                </div>

                {/* Column Body */}
                <Droppable droppableId={columnId}>
                  {(provided, snapshot) => (
                    <div
                      {...provided.droppableProps}
                      ref={provided.innerRef}
                      className={[
                        "flex-1 bg-[#0b1326]/60 backdrop-blur-xl border-x border-b border-white/10 p-3 flex flex-col gap-3 min-h-[150px] transition-colors",
                        snapshot.isDraggingOver ? "bg-[#0b1326]/80 border-cyan-500/30" : ""
                      ].join(" ")}
                    >
                      {columnTasks.map((task, index) => (
                        <Draggable key={task.id} draggableId={task.id} index={index}>
                          {(provided, snapshot) => (
                            <div
                              ref={provided.innerRef}
                              {...provided.draggableProps}
                              {...provided.dragHandleProps}
                              style={provided.draggableProps.style}
                              className={[
                                "relative bg-white/5 border border-white/10 p-4 transition-all group cursor-pointer",
                                // 0px border radius, Glass minimal
                                "rounded-none",
                                // Tonal Layering on hover / drag
                                snapshot.isDragging ? "bg-white/10 shadow-2xl border-cyan-400 opacity-90 z-50 scale-105" : "hover:bg-white/10 hover:border-cyan-500/30",
                                // Focus fence (vertical primary accent)
                                "before:absolute before:inset-y-0 before:left-0 before:w-[2px] before:bg-cyan-400 before:opacity-0 hover:before:opacity-100 before:transition-opacity",
                                snapshot.isDragging ? "before:opacity-100" : ""
                              ].join(" ")}
                              onClick={() => {
                                // Don't open edit if we just finished dragging
                                if (!snapshot.isDragging) setEditingTask(task);
                              }}
                            >
                              <div className="flex justify-between items-start mb-2">
                                <div className="flex items-center gap-2">
                                  <span className="font-mono text-[11px] text-cyan-500 font-bold bg-cyan-500/10 px-1.5 py-0.5">
                                    {task.id}
                                  </span>
                                  <button
                                    onClick={(e) => { e.stopPropagation(); handleDeleteTask(task.id); }}
                                    className="opacity-0 group-hover:opacity-100 transition-opacity p-1 hover:bg-red-500/20 text-red-500/60 hover:text-red-400"
                                    title="Delete Task"
                                  >
                                    <Trash2 className="w-3.5 h-3.5" />
                                  </button>
                                  <button
                                    onClick={(e) => { e.stopPropagation(); setEditingTask(task); }}
                                    className="opacity-0 group-hover:opacity-100 transition-opacity p-1 hover:bg-cyan-500/20 text-cyan-500/60 hover:text-cyan-400"
                                    title="Edit Task"
                                  >
                                    <Pencil className="w-3.5 h-3.5" />
                                  </button>
                                </div>
                                {task.priority === "High" && (
                                  <span className="font-mono text-[10px] text-[#EE9800] bg-[#EE9800]/10 px-1.5 py-0.5 border border-[#EE9800]/20 flex items-center gap-1 uppercase">
                                    <Zap className="w-3 h-3" /> HIGH
                                  </span>
                                )}
                              </div>
                              <h3 className="font-display text-[14px] font-semibold text-slate-100 mb-1.5 leading-tight">
                                {task.title}
                              </h3>
                              <p className="font-display text-[13px] text-slate-400 line-clamp-2 mb-4 leading-relaxed">
                                {task.description}
                              </p>
                              <div className="flex items-center justify-between border-t border-white/5 pt-3">
                                <div className="flex items-center gap-2 text-slate-300">
                                  <User className="w-3.5 h-3.5 opacity-60" />
                                  <span className="font-mono text-[11px] uppercase tracking-widest">{task.assignee}</span>
                                </div>
                                <div className="flex items-center gap-1.5">
                                  <Clock className="w-3.5 h-3.5 text-cyan-500 opacity-60" />
                                  <span className="font-mono text-[11px] text-cyan-400">{task.points} PT</span>
                                </div>
                              </div>
                            </div>
                          )}
                        </Draggable>
                      ))}
                      {provided.placeholder}
                    </div>
                  )}
                </Droppable>
              </div>
            );
          })}
        </DragDropContext>
      </div>
    </div>
  );
}
