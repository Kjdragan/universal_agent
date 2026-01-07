"""
Interview Tool for Harness V2 Planning Phase.

Provides structured question/answer functionality for clarifying ambiguous
massive task requests before execution begins.

Adapted from Claude SDK AskUserQuestion pattern.
"""

import json
from typing import Any
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table


console = Console()


def ask_user_questions(questions: list[dict[str, Any]]) -> dict[str, str]:
    """
    Present structured questions to the user via CLI and collect answers.
    
    Args:
        questions: List of question objects, each containing:
            - question (str): The full question text
            - header (str): Short label (max 12 chars)
            - options (list): Available choices with label and description
            - multiSelect (bool): Allow multiple selections
    
    Returns:
        dict: Mapping of question text to selected answer(s)
    
    Example:
        questions = [
            {
                "question": "What date range should the research cover?",
                "header": "Date Range",
                "options": [
                    {"label": "Last 7 days", "description": "Most recent news"},
                    {"label": "Last 30 days", "description": "Broader context"}
                ],
                "multiSelect": False
            }
        ]
        answers = ask_user_questions(questions)
        # answers = {"What date range should the research cover?": "Last 7 days"}
    """
    import sys
    import termios
    import time
    
    answers = {}
    
    # Flush stdin to clear any leftover characters from multi-line objective paste
    try:
        termios.tcflush(sys.stdin, termios.TCIFLUSH)
        time.sleep(0.1)  # Small delay to ensure buffer is cleared
    except Exception:
        pass  # Ignore if not a TTY
    
    console.print(Panel.fit(
        "[bold cyan]📋 Planning Phase: Clarification Required[/bold cyan]\n"
        "Please answer the following questions to help define the mission.",
        border_style="cyan"
    ))
    
    for i, q in enumerate(questions, 1):
        question_text = q["question"]
        header = q.get("header", f"Q{i}")
        options = q.get("options", [])
        multi_select = q.get("multiSelect", False)
        
        # Build options table
        table = Table(title=f"[bold]{header}[/bold]: {question_text}", show_header=True)
        table.add_column("#", style="dim", width=3)
        table.add_column("Option", style="cyan")
        table.add_column("Description", style="dim")
        
        for idx, opt in enumerate(options, 1):
            table.add_row(str(idx), opt["label"], opt.get("description", ""))
        
        # Add "Other" option
        table.add_row(str(len(options) + 1), "Other", "Specify a custom answer")
        
        console.print(table)
        
        # Input validation loop - keep asking until we get valid input
        while True:
            if multi_select:
                console.print("[dim]Enter comma-separated numbers for multiple selections (e.g., 1,3)[/dim]")
                selection = Prompt.ask("Your selection(s)")
                
                # Validate input is not empty
                if not selection or not selection.strip():
                    console.print("[yellow]Please enter a valid selection.[/yellow]")
                    continue
                    
                selected_indices = [int(x.strip()) - 1 for x in selection.split(",") if x.strip().isdigit()]
                if not selected_indices:
                    console.print("[yellow]Please enter valid number(s).[/yellow]")
                    continue
                    
                selected_labels = []
                for idx in selected_indices:
                    if 0 <= idx < len(options):
                        selected_labels.append(options[idx]["label"])
                    elif idx == len(options):  # "Other"
                        custom = Prompt.ask("Please specify")
                        selected_labels.append(custom)
                        
                if selected_labels:
                    answers[question_text] = ", ".join(selected_labels)
                    break
                else:
                    console.print("[yellow]No valid options selected. Try again.[/yellow]")
            else:
                selection = Prompt.ask("Your selection (number)")
                
                # Validate input is not empty
                if not selection or not selection.strip():
                    console.print("[yellow]Please enter a valid selection.[/yellow]")
                    continue
                    
                try:
                    idx = int(selection) - 1
                    if 0 <= idx < len(options):
                        answers[question_text] = options[idx]["label"]
                        break
                    elif idx == len(options):  # "Other"
                        custom = Prompt.ask("Please specify")
                        answers[question_text] = custom
                        break
                    else:
                        console.print(f"[yellow]Please enter a number between 1 and {len(options) + 1}.[/yellow]")
                except ValueError:
                    # Treat as custom input
                    answers[question_text] = selection
                    break
        
        console.print()
    
    console.print(Panel.fit(
        "[bold green]✅ Clarification complete![/bold green]",
        border_style="green"
    ))
    
    return answers



def present_plan_summary(mission: dict[str, Any]) -> bool:
    """
    Present the generated mission plan to the user for approval.
    
    Args:
        mission: The mission.json dictionary
    
    Returns:
        bool: True if user approves, False if they want changes
    """
    console.print(Panel.fit(
        "[bold cyan]📝 Mission Plan Summary[/bold cyan]\n"
        "Please review the planned tasks before execution begins.",
        border_style="cyan"
    ))
    
    console.print(f"\n[bold]Mission:[/bold] {mission.get('mission_root', 'Unknown')}\n")
    
    # Show clarifications if any
    clarifications = mission.get("clarifications", [])
    if clarifications:
        console.print("[bold]Clarifications:[/bold]")
        for c in clarifications:
            console.print(f"  • {c['question']}: [cyan]{c['answer']}[/cyan]")
        console.print()
    
    # Show tasks
    tasks = mission.get("tasks", [])
    table = Table(title="Planned Tasks", show_header=True)
    table.add_column("#", style="dim", width=3)
    table.add_column("Description", style="white")
    table.add_column("Use Case", style="cyan")
    table.add_column("Success Criteria", style="dim")
    
    for task in tasks:
        table.add_row(
            task.get("id", "?"),
            task.get("description", ""),
            task.get("use_case", "general"),
            task.get("success_criteria", "")[:40] + "..." if len(task.get("success_criteria", "")) > 40 else task.get("success_criteria", "")
        )
    
    console.print(table)
    console.print()
    
    return Confirm.ask("[bold]Approve this plan and begin execution?[/bold]", default=True)


if __name__ == "__main__":
    # Test the interview tool
    test_questions = [
        {
            "question": "What date range should the research cover?",
            "header": "Date Range",
            "options": [
                {"label": "Last 7 days", "description": "Most recent news"},
                {"label": "Last 30 days", "description": "Broader context"},
                {"label": "Custom", "description": "Specify a range"}
            ],
            "multiSelect": False
        },
        {
            "question": "Which countries should be included?",
            "header": "Countries",
            "options": [
                {"label": "Venezuela", "description": ""},
                {"label": "Ecuador", "description": ""},
                {"label": "Colombia", "description": ""}
            ],
            "multiSelect": True
        }
    ]
    
    answers = ask_user_questions(test_questions)
    print("\n--- Collected Answers ---")
    print(json.dumps(answers, indent=2))
