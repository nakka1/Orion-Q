"""Operator-facing descriptions shared by native and embedded controls."""

_GROUPS = (
    (("close", "esc", "✕"), "Close this panel and return to the workspace."),
    (("+",), "Zoom in on the map around its centre."),
    (("−",), "Zoom out on the map around its centre."),
    (("reset view",), "Fit the declared node locations in the map; use the municipality when none are located."),
    (("⋯",), "Show the additional actions that do not fit in this toolbar."),
    (("▾ journal · terminal",), "Expand or collapse the event journal and local command input."),
    (("data source ▾",), "Choose a demonstration, local service or measured input for the workspace."),
    (("open workspace →",), "Open the workspace using the selected data source."),
    (("command center",), "Read recorded commands and compare an unresolved receipt with the current local inventory."),
    (("decision audit",), "Inspect recorded simulation decisions, safety checks and executor receipts."),
    (("context inspection",), "Inspect the policy context and explicitly capture an input without executing a decision."),
    (("policy review",), "Prepare and review a policy proposal, its evidence, approvals and simulation activation."),
    (("polarisation loop",), "Review a compensation policy and run its software simulation with captured receipts."),
    (("open model catalog",), "Inspect optional model packages and verify a local ZIP before offline use."),
    (("ml model",), "Select the optional model decision source when a verified package is available."),
    (("deterministic",), "Select the deterministic decision source for the simulation."),
    (("flash unavailable",), "Hardware flashing is unavailable; physical actuation remains locked."),
    (("run simulated scenario",), "Run the selected local simulation scenario and read back its recorded result."),
    (("replay wdm model", "replay model"), "Replay captured optical inputs through the optional model in software."),
    (("reload source", "refresh"), "Read the latest state from the selected local data source."),
    (("recalibrate",), "Run the selected node's calibration in the simulator and read back the result."),
    (("isolate",), "Isolate the selected node in the simulated network and recalculate affected routes."),
    (("restore",), "Restore the selected node to the simulated network and recalculate affected routes."),
    (("acknowledge",), "Record acknowledgment of the selected alert in the local journal."),
    (("set location", "set location…"), "Declare this node's coordinates so it can appear on the geographic map."),
    (("return to queue",), "Clear this node's geographic location while retaining its inventory and connections."),
    (("pick on map",), "Select coordinates by clicking the map, then review them before applying."),
    (("show on map",), "Open the topology view and select this node on the map or in the location queue."),
    (("node detail →",), "Open the selected node's full status, connections and available actions."),
    (("telemetry →",), "Open the selected connection's fidelity, decoherence and source evidence."),
    (("disable", "disable fibre"), "Disable this fibre in the simulated inventory and recalculate affected routes."),
    (("enable", "enable fibre"), "Enable this fibre in the simulated inventory and recalculate affected routes."),
    (("add to network",), "Add the reviewed node to the local network inventory; no physical device is provisioned."),
    (("remove shadow record",), "Remove the selected temporary shadow entry from the workspace."),
    (("edit links", "configure simulated links"), "Review and save the simulated links between inventory nodes."),
    (("save configuration",), "Persist the reviewed simulated link configuration and read it back."),
    (("reload form",), "Discard the current form edits and read the latest saved configuration."),
    (("run path",), "Configure an endpoint–repeater–endpoint simulation with explicit assumptions."),
    (("new request / reload form",), "Load the current topology version and create a new path request ID."),
    (("run frozen path",), "Simulate the explicit path on its pinned topology version and show the persisted result and receipt."),
    (("temporal campaigns",), "Review, run and inspect recorded campaigns of temporal path simulations."),
    (("request pair",), "Request acquisition of the selected pair in the simulator."),
    (("purify",), "Run the simulated purification step for the selected pair when its state permits it."),
    (("consume",), "Consume the selected pair in the simulator and record its new lifecycle state."),
    (("inject input event",), "Apply the chosen input event to the demonstration and observe the resulting network state."),
    (("read latest 20",), "Read the latest 20 recorded simulation cycles without running new cycles."),
    (("explain selected",), "Verify and explain the selected captured decision using its recorded request ID and hash."),
    (("check current state",), "Compare the selected unresolved command with the current inventory without retrying it."),
    (("load context",), "Capture the local policy and runtime context for inspection."),
    (("observe input",), "Capture an input snapshot for inspection without deciding or executing."),
    (("load baseline",), "Read the baseline policy and evidence used to prepare a proposal."),
    (("prepare",), "Prepare a policy candidate for review without activating it."),
    (("review",), "Validate the prepared policy and its supporting evidence before approval."),
    (("approve proposal", "approve…", "record approval"), "Record explicit approval of the reviewed policy proposal in the local journal."),
    (("activate…",), "Open a confirmation that identifies the approved policy and its simulation scope."),
    (("activate in simulation",), "Activate the approved policy in software and record the activation receipt."),
    (("read active", "read active policy"), "Read the persisted active policy and its activation evidence."),
    (("prepare rollback",), "Prepare a reviewed return to the previous recorded policy revision."),
    (("inspect recovery",), "Inspect the failed activation and the evidence needed for recovery."),
    (("confirm recovery…",), "Review the recovery confirmation before keeping the recorded policy and resuming."),
    (("keep policy and recover",), "Keep the reviewed policy and record the explicitly confirmed software recovery."),
    (("run loop · 20 s",), "Run the reviewed compensation loop for 20 simulated seconds and capture its receipt."),
    (("compare controllers", "compare controllers · reviewed scenarios"), "Compare the controllers on the reviewed scenarios using the same captured assumptions."),
    (("verify local zip", "verify qnet zip"), "Check the selected optional model ZIP against the required package integrity checks."),
    (("forget check",), "Clear the captured package verification result without deleting the ZIP."),
    (("offline session",), "Open an offline model session with explicit input and result inspection."),
    (("synchronizer · live artificial shadow", "start shadow"), "Start the optional model's artificial shadow session and observe software-only results."),
    (("stop / clear",), "Stop the shadow session and clear its current captured display."),
    (("load json",), "Choose and validate the local JSON input for the offline session."),
    (("demo",), "Load the artificial demonstration input into this session."),
    (("run",), "Evaluate the explicitly loaded input in the offline model session."),
    (("select evidence",), "Choose the local evidence file to verify and review with the policy."),
    (("load manifest",), "Choose and validate a temporal campaign manifest before running it."),
    (("edit manifest · in-memory draft",), "Edit a draft campaign manifest for validation before saving or running it."),
    (("save manifest as · new json",), "Save the validated draft as a new local manifest JSON file."),
    (("validate / apply to review",), "Validate the draft and apply it to the campaign review form."),
    (("cancel draft",), "Discard this draft and return to the campaign review."),
    (("run campaign",), "Run the reviewed temporal campaign in an isolated software worker and verify its receipts."),
    (("cancel",), "Cancel the pending operation where cancellation is available, or dismiss this confirmation."),
    (("newer",), "Read the next page of more recent recorded entries."),
    (("older",), "Read the next page of older recorded entries."),
    (("open / verify",), "Open the selected recorded campaign and verify its stored evidence."),
    (("open linked receipt · selected attempt",), "Open and verify the receipt linked to the selected attempt."),
    (("choose / verify file",), "Choose an exported attempt file and verify its structure and integrity."),
    (("choose / verify page zip",), "Choose an exported attempt page ZIP and verify its files and integrity."),
    (("geo",), "Show located nodes on the geographic map and unlocated nodes in the queue."),
    (("graph",), "Show the logical network with its actual inventory connections, independent of coordinates."),
    (("matrix",), "Inspect direct inventory connections in the node-by-node adjacency matrix."),
    (("physical", "fibres"), "Show the configured fibres and their current simulated operating state."),
    (("entanglement", "pairs"), "Inspect simulated pair states, demands and the selected routed path."),
    (("dark",), "Use the dark interface theme."),
    (("light",), "Use the light interface theme."),
    (("endpoint", "repeater", "core"), "Choose this role for the node being added; review its connections separately."),
)

_HELP = {name: description for aliases, description in _GROUPS for name in aliases}

_NAVIGATION = {
    "topology": "Inspect nodes, fibres, routes and their current simulated state.",
    "telemetry": "Inspect fidelity, decoherence, optical inputs and model replay evidence.",
    "keys": "Inspect key sessions, their source evidence and exports.",
    "node": "Inspect node details, add inventory nodes and review their connections.",
    "communicators": "Cadastrar e consultar dispositivos, adaptadores, protocolos e capacidades de comunicação. Operação física somente por fontes e integrações validadas.",
    "fpga": "Review edge policies, optional models and recorded simulation decisions.",
    "audit": "Inspect local events, command receipts and access evidence.",
    "signout": "Return to the workspace entry screen.",
    "palette": "Open the command palette to search available navigation and local actions.",
}

_OPERATIONS = {
    "configure_simulated_link": _HELP["edit links"],
    "run_workspace_path": _HELP["run path"],
    "run_temporal_path": _HELP["run path"],
    "read_temporal_campaign_history": _HELP["temporal campaigns"],
    "set_node_isolation": _HELP["isolate"],
    "set_node_location": _HELP["set location"],
    "set_link_active": _HELP["disable fibre"],
    "add_network_node": _HELP["add to network"],
    "add_simulated_node": "Persist the reviewed node in the local simulated inventory.",
    "stage_node": "Stage this temporary node in the shadow workspace for inspection.",
}


def button_description(value, operation=None):
    key = value.strip().casefold()
    if key in _HELP:
        return _HELP[key]
    if key.startswith("export"):
        return "Save the captured evidence to a local file for inspection; this does not run another simulation."
    if operation in _OPERATIONS:
        return _OPERATIONS[operation]
    if not key:
        return "Select this entry and inspect its details in the current panel."
    return f"Select {value} and inspect its details or settings in this panel."


def navigation_description(kind):
    return _NAVIGATION.get(kind, "Open this workspace section.")


def install_control_help(root):
    """Cover Qt's own tab arrows, clear buttons and option checkboxes too."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QAbstractButton, QTabBar, QLineEdit

    root.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)
    for button in root.findChildren(QAbstractButton):
        if button.toolTip():
            continue
        name = button.objectName().casefold()
        if 'left' in name or 'left' in button.accessibleName().casefold():
            description = 'Reveal the earlier tabs that do not fit in this panel.'
        elif 'right' in name or 'right' in button.accessibleName().casefold():
            description = 'Reveal the later tabs that do not fit in this panel.'
        elif isinstance(button.parentWidget(), QLineEdit):
            description = 'Clear the current text in this input field.'
        elif button.isCheckable():
            description = f'Enable or disable this option: {button.text() or button.accessibleName()}.'
        else:
            description = button_description(button.text() or button.accessibleName())
        button.setToolTip(description)
    for bar in root.findChildren(QTabBar):
        for index in range(bar.count()):
            if not bar.tabToolTip(index):
                bar.setTabToolTip(index, f'Open {bar.tabText(index)} in this panel.')
