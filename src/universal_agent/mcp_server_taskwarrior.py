from mcp.server.fastmcp import FastMCP
from tasklib import TaskWarrior
import logging
import json
import shutil
import os

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp_server_taskwarrior")

# Check if taskwarrior is installed
HAS_TASKWARRIOR = shutil.which("task") is not None

mcp = FastMCP("Taskwarrior Toolkit")

def _get_tw():
    if not HAS_TASKWARRIOR:
        raise RuntimeError("Taskwarrior ('task') is not installed. Please install it (e.g., 'sudo apt install taskwarrior').")
    # Initialize with default config (looks for ~/.taskrc)
    return TaskWarrior()

@mcp.tool()
def task_add(description: str, due: str = None, priority: str = None, project: str = None, tags: list[str] = None) -> str:
    """
    Add a new task to Taskwarrior.
    
    Args:
        description: The task description.
        due: Due date (e.g., 'tomorrow', 'friday', '2023-12-31').
        priority: Priority ('H', 'M', 'L', or None).
        project: Project name.
        tags: List of tags.
    """
    try:
        tw = _get_tw()
        task = tw.tasks.new(description=description)
        if due:
            task['due'] = due
        if priority:
            task['priority'] = priority
        if project:
            task['project'] = project
        if tags:
            task['tags'] = tags
        task.save()
        return f"Task created: ID {task['id']} - {task['description']}"
    except Exception as e:
        return f"Error adding task: {str(e)}"

@mcp.tool()
def task_list(filter_str: str = "android status:pending") -> str:
    """
    List tasks matching a filter. Default shows pending tasks.
    
    Args:
        filter_str: Taskwarrior filter string (e.g. 'project:Home status:pending').
                    'android' is usually implied for standard filtering but we default to 'status:pending'.
    """
    try:
        tw = _get_tw()
        # Tasklib filtering is a bit different, but we can use filter().
        # However, complex string filters are better passed as args to the binary if using CLI,
        # but tasklib supports kwargs.
        # For simplicity in this tool, we'll try to parse basic kv or just return pending.
        
        # ACTUALLY, tasklib allows filtering by kwargs.
        # To support raw filter strings like "project:A", we might need to parse.
        # let's just fetch pending and show top 20 for now, or allow specific kwargs.
        
        # Let's support a slightly more robust list for LLM usage:
        tasks = tw.tasks.filter(status='pending')
        
        # If user provided specific constraints, we ideally want to apply them.
        # But parsing "project:Home" reliably is tricky without the CLI parser.
        # We will retrieve ALL pending tasks and format them as JSON text for the LLM to process.
        
        output = []
        for t in tasks:
            entry = {
                "id": t['id'],
                "description": t['description'],
                "status": t['status'],
                "uuid": t['uuid'],
            }
            if t['due']:
                entry['due'] = str(t['due'])
            if t['project']:
                entry['project'] = str(t['project'])
            if t['priority']:
                entry['priority'] = t['priority']
            if t['tags']:
                entry['tags'] = list(t['tags'])
                
            output.append(entry)
            
        # Basic text formatting
        lines = [f"Found {len(output)} pending tasks:"]
        for item in output:
            meta = []
            if item.get('due'): meta.append(f"due:{item['due']}")
            if item.get('priority'): meta.append(f"pri:{item['priority']}")
            if item.get('project'): meta.append(f"proj:{item['project']}")
            
            line = f"{item['id']}. {item['description']}"
            if meta:
                line += f" ({', '.join(meta)})"
            lines.append(line)
            
        return "\n".join(lines)

    except Exception as e:
        return f"Error listing tasks: {str(e)}"

@mcp.tool()
def task_done(id: int) -> str:
    """
    Mark a task as completed by ID.
    
    Args:
        id: The ID of the task.
    """
    try:
        tw = _get_tw()
        # Find task by ID. tasklib ID lookup can be tricky as IDs change.
        # But tw.tasks.get(id=id) usually works for active tasks.
        try:
            task = tw.tasks.get(id=id)
            task.done()
            return f"Task {id} marked as done."
        except tasklib.Task.DoesNotExist:
             return f"Task ID {id} not found."
    except Exception as e:
        return f"Error completing task: {str(e)}"

@mcp.tool()
def task_modify(id: int, description: str = None, due: str = None, priority: str = None, project: str = None) -> str:
    """
    Modify an existing task.
    
    Args:
        id: Task ID.
        description: New description.
        due: New due date.
        priority: New priority.
        project: New project.
    """
    try:
        tw = _get_tw()
        task = tw.tasks.get(id=id)
        
        updates = []
        if description:
            task['description'] = description
            updates.append("description")
        if due:
            task['due'] = due
            updates.append("due")
        if priority:
            task['priority'] = priority
            updates.append("priority")
        if project:
            task['project'] = project
            updates.append("project")
            
        task.save()
        return f"Task {id} updated: {', '.join(updates)}"
    except Exception as e:
        return f"Error modifying task: {str(e)}"

if __name__ == "__main__":
    mcp.run()
