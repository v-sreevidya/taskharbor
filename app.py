import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024


class DatabaseNotConfigured(Exception):
    pass


def connect_db():
    database_url = os.environ.get("DATABASE_URL")

    if not database_url:
        raise DatabaseNotConfigured()

    return psycopg.connect(
        database_url,
        connect_timeout=3,
        options="-c statement_timeout=5000",
        row_factory=dict_row,
    )


PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Task Tracker</title>
    <style>
        * { box-sizing: border-box; }
        body {
            margin: 0;
            background: #f3f5fa;
            color: #172033;
            font-family: system-ui, sans-serif;
        }
        main {
            max-width: 680px;
            margin: 70px auto;
            padding: 32px;
            background: white;
            border-radius: 16px;
        }
        h1 { margin-bottom: 8px; }
        .muted { color: #64748b; }
        #status {
            padding: 12px;
            background: #f1f5f9;
            border-radius: 8px;
        }
        form, li {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        input[type="text"] {
            flex: 1;
            min-width: 0;
            padding: 12px;
            border: 1px solid #cbd5e1;
            border-radius: 8px;
        }
        button {
            border: none;
            border-radius: 8px;
            padding: 12px 16px;
            background: #2563eb;
            color: white;
            cursor: pointer;
        }
        button:disabled { opacity: .5; cursor: not-allowed; }
        ul { padding: 0; list-style: none; }
        li { padding: 16px 0; border-bottom: 1px solid #e2e8f0; }
        li span { overflow-wrap: anywhere; }
        .done { text-decoration: line-through; color: #64748b; }
        @media (max-width: 720px) {
            main { margin: 20px; padding: 20px; }
        }
    </style>
</head>
<body>
<main>
    <p class="muted">MY WORKSPACE</p>
    <h1>Task Tracker</h1>
    <p class="muted">Create tasks and track your progress.</p>

    <p id="status" role="status">Connecting to the application...</p>

    <form id="task-form">
        <input id="title" type="text" maxlength="200"
               placeholder="Enter a new task" required>
        <button id="add" type="submit" disabled>Add task</button>
    </form>

    <ul id="tasks"></ul>
    <p id="empty" class="muted"></p>
    <button id="refresh" type="button">Refresh</button>
</main>

<script>
    const status = document.getElementById("status");
    const addButton = document.getElementById("add");
    const empty = document.getElementById("empty");
    const list = document.getElementById("tasks");

    async function api(path, method = "GET", body) {
        const response = await fetch(path, {
            method,
            headers: {"Content-Type": "application/json"},
            body: body === undefined ? undefined : JSON.stringify(body)
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || "Request failed");
        }

        return data;
    }

    async function loadTasks() {
        addButton.disabled = true;
        status.textContent = "Loading tasks...";
        list.replaceChildren();
        empty.textContent = "";

        try {
            const data = await api("/api/tasks");

            for (const task of data.tasks) {
                const item = document.createElement("li");

                const checkbox = document.createElement("input");
                checkbox.type = "checkbox";
                checkbox.checked = task.completed;

                const title = document.createElement("span");
                title.textContent = task.title;
                if (task.completed) title.className = "done";

                checkbox.onchange = async () => {
                    checkbox.disabled = true;

                    try {
                        await api(`/api/tasks/${task.id}`, "PATCH", {
                            completed: checkbox.checked
                        });
                        await loadTasks();
                    } catch (error) {
                        checkbox.checked = task.completed;
                        status.textContent = error.message;
                    } finally {
                        checkbox.disabled = false;
                    }
                };

                item.append(checkbox, title);
                list.append(item);
            }

            status.textContent = "Database connected";
            empty.textContent = data.tasks.length ? "" : "No tasks yet.";
            addButton.disabled = false;
        } catch (error) {
            status.textContent = error.message;
            empty.textContent = "Task data is currently unavailable.";
        }
    }

    document.getElementById("task-form").onsubmit = async (event) => {
        event.preventDefault();
        const input = document.getElementById("title");
        addButton.disabled = true;

        try {
            await api("/api/tasks", "POST", {title: input.value});
            input.value = "";
            await loadTasks();
        } catch (error) {
            status.textContent = error.message;
        } finally {
            addButton.disabled = false;
        }
    };

    document.getElementById("refresh").onclick = loadTasks;
    loadTasks();
</script>
</body>
</html>
"""


@app.get("/")
def home():
    return render_template_string(PAGE)


@app.get("/health")
def health():
    # Application liveness does not depend on the database.
    return jsonify(status="alive")


@app.get("/ready")
def ready():
    # Full application readiness requires the database table.
    with connect_db() as connection:
        connection.execute("SELECT id FROM tasks LIMIT 1")

    return jsonify(status="ready")


@app.get("/api/tasks")
def list_tasks():
    with connect_db() as connection:
        tasks = connection.execute("""
            SELECT id, title, completed
            FROM tasks
            ORDER BY id DESC
            LIMIT 100
            """).fetchall()

    return jsonify(tasks=tasks)


@app.post("/api/tasks")
def create_task():
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify(error="A JSON object is required"), 400

    title = data.get("title")

    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200:
        return jsonify(error="Enter a task between 1 and 200 characters"), 400

    with connect_db() as connection:
        task = connection.execute(
            """
            INSERT INTO tasks (title)
            VALUES (%s)
            RETURNING id, title, completed
            """,
            (title.strip(),),
        ).fetchone()

    return jsonify(task), 201


@app.patch("/api/tasks/<int:task_id>")
def update_task(task_id):
    data = request.get_json(silent=True)

    if not isinstance(data, dict) or type(data.get("completed")) is not bool:
        return jsonify(error="completed must be true or false"), 400

    with connect_db() as connection:
        task = connection.execute(
            """
            UPDATE tasks
            SET completed = %s
            WHERE id = %s
            RETURNING id, title, completed
            """,
            (data["completed"], task_id),
        ).fetchone()

    if task is None:
        return jsonify(error="Task not found"), 404

    return jsonify(task)


@app.errorhandler(DatabaseNotConfigured)
def database_not_configured(error):
    return jsonify(error="Database not configured"), 503


@app.errorhandler(psycopg.Error)
def database_error(error):
    app.logger.error("Database error: %s", type(error).__name__)
    return jsonify(error="Database unavailable or not initialized"), 503


@app.cli.command("init-db")
def init_db():
    """Run explicitly after the database has been provisioned."""
    schema = Path(__file__).with_name("schema.sql").read_text()

    with connect_db() as connection:
        connection.execute(schema)

    print("Database table initialized.")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
