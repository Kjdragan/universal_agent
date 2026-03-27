"use client";

import { useEffect, useState } from "react";
import { DragDropContext, Droppable, Draggable, DropResult } from "@hello-pangea/dnd";
import { CopyPlus, Clock, Zap, User, Trash2 } from "lucide-react";

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

const INITIAL_TASKS: Task[] = [
  {
    id: "TASK-100",
    title: "Implement Heartbeat Subsystem",
    description: "Refactor existing heartbeat listener to handle concurrent pulses across the mesh.",
    status: "Backlog",
    priority: "High",
    assignee: "Cora",
    points: 8,
  },
  {
    id: "TASK-101",
    title: "Update Gateway Schema",
    description: "Align the Postgres configuration schema with new API v2 specifications.",
    status: "Backlog",
    priority: "Medium",
    assignee: "Simon",
    points: 3,
  },
  {
    id: "TASK-102",
    title: "Deploy AgentMail Handlers",
    description: "Finalize deployment scripts for the new AgentMail ingest hooks.",
    status: "In Progress",
    priority: "High",
    assignee: "Cora",
    points: 5,
  },
  {
    id: "TASK-103",
    title: "CSI Feed Rendering Glitch",
    description: "Fix layout shift when incoming events have large multiline payloads.",
    status: "In Review",
    priority: "Low",
    assignee: "UI Engine",
    points: 2,
  },
  {
    id: "TASK-104",
    title: "Migrate Auth to Infisical",
    description: "Completely migrate secrets to Infisical across the production environment.",
    status: "Done",
    priority: "High",
    assignee: "SysOps",
    points: 8,
  },
];

const COLUMNS: ColumnType[] = ["Backlog", "In Progress", "In Review", "Done"];

export default function KanbanBoard() {
  const [tasks, setTasks] = useState<Task[]>(INITIAL_TASKS);
  const [isMounted, setIsMounted] = useState(false);

  useEffect(() => {
    setIsMounted(true);
  }, []);

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
  };

  const handleDeleteTask = (taskId: string) => {
    setTasks(tasks.filter((t) => t.id !== taskId));
  };

  const onDragEnd = (result: DropResult) => {
    if (!result.destination) return;
    const { source, destination, draggableId } = result;

    if (source.droppableId === destination.droppableId && source.index === destination.index) {
      return;
    }

    const draggedTask = tasks.find((t) => t.id === draggableId);
    if (!draggedTask) return;

    const sourceStatus = source.droppableId as ColumnType;
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
                                "relative bg-white/5 border border-white/10 p-4 transition-all group",
                                // 0px border radius, Glass minimal
                                "rounded-none",
                                // Tonal Layering on hover / drag
                                snapshot.isDragging ? "bg-white/10 shadow-2xl border-cyan-400 opacity-90 z-50 scale-105" : "hover:bg-white/10 hover:border-cyan-500/30",
                                // Focus fence (vertical primary accent)
                                "before:absolute before:inset-y-0 before:left-0 before:w-[2px] before:bg-cyan-400 before:opacity-0 hover:before:opacity-100 before:transition-opacity",
                                snapshot.isDragging ? "before:opacity-100" : ""
                              ].join(" ")}
                            >
                              <div className="flex justify-between items-start mb-2">
                                <div className="flex items-center gap-2">
                                  <span className="font-mono text-[11px] text-cyan-500 font-bold bg-cyan-500/10 px-1.5 py-0.5">
                                    {task.id}
                                  </span>
                                  <button
                                    onClick={() => handleDeleteTask(task.id)}
                                    className="opacity-0 group-hover:opacity-100 transition-opacity p-1 hover:bg-red-500/20 text-red-500/60 hover:text-red-400"
                                    title="Delete Task"
                                  >
                                    <Trash2 className="w-3.5 h-3.5" />
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
