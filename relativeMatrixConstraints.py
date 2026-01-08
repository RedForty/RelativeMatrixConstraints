"""
Relative Matrix Constraint UI
-----------------------------
A UI for creating and managing matrix-based relative offset constraints
between two skeleton/prop pairs. Constraints are tracked via network nodes
for persistence across Maya sessions.
"""

import maya.cmds as cmds
import maya.mel as mel
import maya.api.OpenMaya as om2
from functools import wraps, partial

# Qt imports - handle Maya version differences
try:
    from PySide6 import QtWidgets, QtCore, QtGui
except ImportError:
    from PySide2 import QtWidgets, QtCore, QtGui

try:
    from shiboken6 import wrapInstance
except ImportError:
    from shiboken2 import wrapInstance

import maya.OpenMayaUI as omui


# =============================================================================
# Constants
# =============================================================================

NETWORK_NODE_IDENTIFIER = "relMatrixConstraintData"
NETWORK_NODE_PREFIX = "relMtxConstraint"


# =============================================================================
# Decorators
# =============================================================================

def viewport_off(func):
    """
    Decorator - turn off Maya display while func is running.
    If func fails, the error will be raised after restoring viewport.
    """
    @wraps(func)
    def wrap(*args, **kwargs):
        # Check if parallel evaluation is on
        parallel = False
        if 'parallel' in cmds.evaluationManager(q=True, mode=True):
            cmds.evaluationManager(mode='off')
            parallel = True
        
        # Turn $gMainPane Off
        mel.eval("paneLayout -e -manage false $gMainPane")
        cmds.refresh(suspend=True)
        # Hide the timeslider
        mel.eval("setTimeSliderVisible 0;")
        
        try:
            return func(*args, **kwargs)
        except Exception:
            raise
        finally:
            cmds.refresh(suspend=False)
            mel.eval("setTimeSliderVisible 1;")
            if parallel:
                cmds.evaluationManager(mode='parallel')
            mel.eval("paneLayout -e -manage true $gMainPane")
            cmds.refresh()
    
    return wrap


# =============================================================================
# Core Functions
# =============================================================================

def get_maya_main_window():
    """Get Maya's main window as a Qt object."""
    main_window_ptr = omui.MQtUtil.mainWindow()
    return wrapInstance(int(main_window_ptr), QtWidgets.QWidget)


@viewport_off
def _bake_with_viewport_off(target, start, end):
    """
    Bake transform attributes with viewport disabled for speed.
    
    Args:
        target: Object to bake
        start: Start frame
        end: End frame
    """
    cmds.bakeResults(
        target,
        simulation=True,
        time=(start, end),
        sampleBy=1,
        preserveOutsideKeys=True,
        sparseAnimCurveBake=False,
        minimizeRotation=True,
        attribute=['tx', 'ty', 'tz', 'rx', 'ry', 'rz']
    )


def find_constraint_network_nodes():
    """
    Find all network nodes in the scene that represent our constraints.
    
    Returns:
        list: Network node names
    """
    network_nodes = cmds.ls(type='network') or []
    constraint_nodes = []
    
    for node in network_nodes:
        if cmds.attributeQuery(NETWORK_NODE_IDENTIFIER, node=node, exists=True):
            constraint_nodes.append(node)
    
    return constraint_nodes


def get_constraint_data(network_node):
    """
    Get the constraint data from a network node.
    
    Args:
        network_node: The network node storing constraint metadata
        
    Returns:
        dict: Constraint data with keys: source_ref, source_driven, target_ref, 
              target_driven, mult_matrix, decompose_matrix, display_name
              Returns None if data is incomplete.
    """
    data = {
        'network_node': network_node,
        'display_name': '',
        'source_ref': None,
        'source_driven': None,
        'target_ref': None,
        'target_driven': None,
        'mult_matrix': None,
        'decompose_matrix': None
    }
    
    # Get display name
    if cmds.attributeQuery('displayName', node=network_node, exists=True):
        data['display_name'] = cmds.getAttr(f"{network_node}.displayName") or ''
    
    # Get connected objects via message attributes
    attr_map = {
        'sourceRef': 'source_ref',
        'sourceDriven': 'source_driven',
        'targetRef': 'target_ref',
        'targetDriven': 'target_driven',
        'multMatrixNode': 'mult_matrix',
        'decomposeMatrixNode': 'decompose_matrix'
    }
    
    for attr_name, data_key in attr_map.items():
        if cmds.attributeQuery(attr_name, node=network_node, exists=True):
            connections = cmds.listConnections(f"{network_node}.{attr_name}", source=True)
            if connections:
                data[data_key] = connections[0]
    
    return data


def create_constraint_network_node(source_ref, source_driven, target_ref, target_driven,
                                    mult_matrix, decompose_matrix):
    """
    Create a network node to store constraint metadata.
    
    Args:
        source_ref: Source reference object (hand1)
        source_driven: Source driven object (prop1)
        target_ref: Target reference object (hand2)
        target_driven: Target driven object (prop2)
        mult_matrix: The multMatrix node
        decompose_matrix: The decomposeMatrix node
        
    Returns:
        str: Name of created network node
    """
    # Create network node
    network_node = cmds.createNode('network', name=f"{NETWORK_NODE_PREFIX}_data#")
    
    # Add identifier attribute
    cmds.addAttr(network_node, longName=NETWORK_NODE_IDENTIFIER, attributeType='bool')
    cmds.setAttr(f"{network_node}.{NETWORK_NODE_IDENTIFIER}", True)
    
    # Add display name attribute
    cmds.addAttr(network_node, longName='displayName', dataType='string')
    display_name = f"{source_driven} → {target_driven}"
    cmds.setAttr(f"{network_node}.displayName", display_name, type='string')
    
    # Add message attributes for connections
    for attr_name in ['sourceRef', 'sourceDriven', 'targetRef', 'targetDriven',
                      'multMatrixNode', 'decomposeMatrixNode']:
        cmds.addAttr(network_node, longName=attr_name, attributeType='message')
    
    # Connect objects to network node
    connections = [
        (source_ref, 'sourceRef'),
        (source_driven, 'sourceDriven'),
        (target_ref, 'targetRef'),
        (target_driven, 'targetDriven'),
        (mult_matrix, 'multMatrixNode'),
        (decompose_matrix, 'decomposeMatrixNode')
    ]
    
    for obj, attr in connections:
        cmds.connectAttr(f"{obj}.message", f"{network_node}.{attr}")
    
    return network_node


def create_relative_matrix_constraint(source_ref, source_driven, target_ref, target_driven):
    """
    Create the matrix constraint network and metadata node.
    
    Args:
        source_ref: Source reference object (hand1)
        source_driven: Source driven object (prop1)
        target_ref: Target reference object (hand2)
        target_driven: Target driven object (prop2)
        
    Returns:
        str: Name of the network node storing constraint data
    """
    name_prefix = NETWORK_NODE_PREFIX
    
    # Create multMatrix node
    mult_matrix = cmds.createNode('multMatrix', name=f"{name_prefix}_multMatrix#")
    
    # Connect matrices in order
    cmds.connectAttr(f"{source_driven}.worldMatrix[0]", f"{mult_matrix}.matrixIn[0]")
    cmds.connectAttr(f"{source_ref}.worldInverseMatrix[0]", f"{mult_matrix}.matrixIn[1]")
    cmds.connectAttr(f"{target_ref}.worldMatrix[0]", f"{mult_matrix}.matrixIn[2]")
    
    # Handle parent space of target_driven
    target_parent = cmds.listRelatives(target_driven, parent=True)
    if target_parent:
        cmds.connectAttr(f"{target_parent[0]}.worldInverseMatrix[0]", f"{mult_matrix}.matrixIn[3]")
    
    # Create decomposeMatrix
    decompose = cmds.createNode('decomposeMatrix', name=f"{name_prefix}_decomposeMatrix#")
    
    cmds.connectAttr(f"{mult_matrix}.matrixSum", f"{decompose}.inputMatrix")
    
    # Connect to target
    cmds.connectAttr(f"{decompose}.outputTranslate", f"{target_driven}.translate")
    cmds.connectAttr(f"{decompose}.outputRotate", f"{target_driven}.rotate")
    
    # Create network node to store metadata
    network_node = create_constraint_network_node(
        source_ref, source_driven, target_ref, target_driven,
        mult_matrix, decompose
    )
    
    return network_node


def delete_constraint(network_node):
    """
    Delete a constraint and its associated nodes.
    
    Args:
        network_node: The network node storing constraint data
    """
    data = get_constraint_data(network_node)
    
    # Disconnect and delete the matrix nodes
    if data['decompose_matrix'] and cmds.objExists(data['decompose_matrix']):
        cmds.delete(data['decompose_matrix'])
    
    if data['mult_matrix'] and cmds.objExists(data['mult_matrix']):
        cmds.delete(data['mult_matrix'])
    
    # Delete the network node
    if cmds.objExists(network_node):
        cmds.delete(network_node)


def remove_connections_from_target(target, keep_anim_curves=False):
    """
    Remove any incoming connections from target transform attributes.
    
    Args:
        target: The object to disconnect
        keep_anim_curves: If True, don't delete animCurve nodes (preserves baked keys)
    """
    attrs = ['translate', 'rotate', 'tx', 'ty', 'tz', 'rx', 'ry', 'rz']
    
    nodes_to_delete = set()
    
    for attr in attrs:
        full_attr = f"{target}.{attr}"
        conns = cmds.listConnections(
            full_attr, 
            source=True, 
            destination=False,
            plugs=True,
            connections=True,
            skipConversionNodes=False
        )
        if conns:
            for i in range(0, len(conns), 2):
                dest_plug = conns[i]
                src_plug = conns[i + 1]
                
                src_node = src_plug.split('.')[0]
                node_type = cmds.nodeType(src_node)
                
                # Skip animCurves if we want to keep them (post-bake cleanup)
                if keep_anim_curves and node_type.startswith('animCurve'):
                    continue
                
                # Disconnect first
                try:
                    cmds.disconnectAttr(src_plug, dest_plug)
                except RuntimeError:
                    pass
                
                # Mark source node for deletion (skip scene objects)
                if node_type not in ['transform', 'joint', 'mesh', 'nurbsCurve', 'nurbsSurface']:
                    nodes_to_delete.add(src_node)
                    
                    if node_type == 'decomposeMatrix':
                        mult_conn = cmds.listConnections(f"{src_node}.inputMatrix", source=True)
                        if mult_conn:
                            nodes_to_delete.add(mult_conn[0])
    
    for node in nodes_to_delete:
        if cmds.objExists(node):
            try:
                cmds.delete(node)
            except RuntimeError:
                pass


# =============================================================================
# Custom Widget for Constraint List Items
# =============================================================================

class ConstraintListItem(QtWidgets.QWidget):
    """Custom widget for displaying a constraint in the list with a bake button."""
    
    bake_clicked = QtCore.Signal(str)  # Emits network_node name
    
    def __init__(self, network_node, display_name, parent=None):
        super().__init__(parent)
        
        self.network_node = network_node
        
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(8)
        
        # Label showing constraint info
        self.label = QtWidgets.QLabel(display_name)
        self.label.setMinimumWidth(200)
        layout.addWidget(self.label, stretch=1)
        
        # Bake button
        self.bake_btn = QtWidgets.QPushButton("Bake")
        self.bake_btn.setFixedWidth(50)
        self.bake_btn.clicked.connect(self._on_bake_clicked)
        layout.addWidget(self.bake_btn)
    
    def _on_bake_clicked(self):
        self.bake_clicked.emit(self.network_node)


# =============================================================================
# Main UI
# =============================================================================

class RelativeMatrixConstraintUI(QtWidgets.QDialog):
    """UI for creating and managing relative matrix constraints."""
    
    WINDOW_TITLE = "Relative Matrix Constraint"
    
    def __init__(self, parent=get_maya_main_window()):
        super().__init__(parent)
        
        self.setWindowTitle(self.WINDOW_TITLE)
        self.setMinimumWidth(450)
        self.setWindowFlags(self.windowFlags() ^ QtCore.Qt.WindowContextHelpButtonHint)
        
        self._build_ui()
        self._connect_signals()
        self._refresh_constraint_list()
    
    def _build_ui(self):
        """Build the UI layout."""
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(10, 10, 10, 10)
        
        # =========================
        # Creation Section (Top)
        # =========================
        
        # Source group
        source_group = QtWidgets.QGroupBox("Source (Skeleton 1)")
        source_layout = QtWidgets.QVBoxLayout(source_group)
        
        self.source_ref_field = self._create_object_row(source_layout, "Reference (Hand):")
        self.source_driven_field = self._create_object_row(source_layout, "Driven (Prop):")
        
        main_layout.addWidget(source_group)
        
        # Target group
        target_group = QtWidgets.QGroupBox("Target (Skeleton 2)")
        target_layout = QtWidgets.QVBoxLayout(target_group)
        
        self.target_ref_field = self._create_object_row(target_layout, "Reference (Hand):")
        self.target_driven_field = self._create_object_row(target_layout, "Driven (Prop):")
        
        main_layout.addWidget(target_group)
        
        # Frame range
        frame_layout = QtWidgets.QHBoxLayout()
        
        frame_layout.addWidget(QtWidgets.QLabel("Frame Range:"))
        
        self.start_frame_spin = QtWidgets.QSpinBox()
        self.start_frame_spin.setRange(-10000, 100000)
        self.start_frame_spin.setValue(int(cmds.playbackOptions(q=True, minTime=True)))
        frame_layout.addWidget(self.start_frame_spin)
        
        frame_layout.addWidget(QtWidgets.QLabel(" to "))
        
        self.end_frame_spin = QtWidgets.QSpinBox()
        self.end_frame_spin.setRange(-10000, 100000)
        self.end_frame_spin.setValue(int(cmds.playbackOptions(q=True, maxTime=True)))
        frame_layout.addWidget(self.end_frame_spin)
        
        frame_layout.addStretch()
        
        self.use_timeline_btn = QtWidgets.QPushButton("Use Timeline")
        self.use_timeline_btn.setFixedWidth(85)
        frame_layout.addWidget(self.use_timeline_btn)
        
        main_layout.addLayout(frame_layout)
        
        # Create/Verify buttons
        create_button_layout = QtWidgets.QHBoxLayout()
        
        self.create_btn = QtWidgets.QPushButton("Create Constraint")
        self.create_btn.setMinimumHeight(32)
        create_button_layout.addWidget(self.create_btn)
        
        self.verify_btn = QtWidgets.QPushButton("Verify")
        self.verify_btn.setMinimumHeight(32)
        self.verify_btn.setFixedWidth(70)
        create_button_layout.addWidget(self.verify_btn)
        
        main_layout.addLayout(create_button_layout)
        
        # =========================
        # Existing Constraints Section (Bottom)
        # =========================
        
        constraints_group = QtWidgets.QGroupBox("Existing Constraints")
        constraints_layout = QtWidgets.QVBoxLayout(constraints_group)
        
        # List widget for constraints
        self.constraints_list = QtWidgets.QListWidget()
        self.constraints_list.setMinimumHeight(100)
        self.constraints_list.setMaximumHeight(200)
        self.constraints_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        constraints_layout.addWidget(self.constraints_list)
        
        # Management buttons
        manage_button_layout = QtWidgets.QHBoxLayout()
        
        self.bake_all_btn = QtWidgets.QPushButton("Bake All")
        self.bake_all_btn.setMinimumHeight(28)
        manage_button_layout.addWidget(self.bake_all_btn)
        
        self.delete_selected_btn = QtWidgets.QPushButton("Delete Selected")
        self.delete_selected_btn.setMinimumHeight(28)
        manage_button_layout.addWidget(self.delete_selected_btn)
        
        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        self.refresh_btn.setMinimumHeight(28)
        self.refresh_btn.setFixedWidth(70)
        manage_button_layout.addWidget(self.refresh_btn)
        
        constraints_layout.addLayout(manage_button_layout)
        
        main_layout.addWidget(constraints_group)
        
        # Status bar
        self.status_label = QtWidgets.QLabel("")
        self.status_label.setStyleSheet("color: gray; font-style: italic;")
        main_layout.addWidget(self.status_label)
    
    def _create_object_row(self, parent_layout, label_text):
        """Create a row with label, text field, and 'Set Selected' button."""
        row_layout = QtWidgets.QHBoxLayout()
        
        label = QtWidgets.QLabel(label_text)
        label.setFixedWidth(100)
        row_layout.addWidget(label)
        
        field = QtWidgets.QLineEdit()
        field.setPlaceholderText("Select object and click <<")
        row_layout.addWidget(field)
        
        set_btn = QtWidgets.QPushButton("<<")
        set_btn.setFixedWidth(30)
        set_btn.setToolTip("Set from selection")
        set_btn.clicked.connect(partial(self._set_from_selection, field))
        row_layout.addWidget(set_btn)
        
        parent_layout.addLayout(row_layout)
        return field
    
    def _connect_signals(self):
        """Connect UI signals to slots."""
        self.create_btn.clicked.connect(self._on_create_constraint)
        self.verify_btn.clicked.connect(self._on_verify)
        self.use_timeline_btn.clicked.connect(self._on_use_timeline)
        self.bake_all_btn.clicked.connect(self._on_bake_all)
        self.delete_selected_btn.clicked.connect(self._on_delete_selected)
        self.refresh_btn.clicked.connect(self._refresh_constraint_list)
        self.constraints_list.itemSelectionChanged.connect(self._on_selection_changed)
    
    def _set_from_selection(self, field):
        """Set the field value from the current Maya selection."""
        sel = cmds.ls(selection=True)
        if sel:
            field.setText(sel[0])
            self._set_status(f"Set: {sel[0]}")
        else:
            self._set_status("Nothing selected", error=True)
    
    def _on_use_timeline(self):
        """Update frame range from Maya's timeline."""
        self.start_frame_spin.setValue(int(cmds.playbackOptions(q=True, minTime=True)))
        self.end_frame_spin.setValue(int(cmds.playbackOptions(q=True, maxTime=True)))
        self._set_status("Frame range updated from timeline")
    
    def _get_objects(self):
        """Get the four objects from the UI fields."""
        return {
            'source_ref': self.source_ref_field.text().strip(),
            'source_driven': self.source_driven_field.text().strip(),
            'target_ref': self.target_ref_field.text().strip(),
            'target_driven': self.target_driven_field.text().strip()
        }
    
    def _validate_objects(self):
        """Validate that all objects exist in Maya."""
        objs = self._get_objects()
        
        for key, value in objs.items():
            if not value:
                self._set_status(f"Missing: {key}", error=True)
                return None
            if not cmds.objExists(value):
                self._set_status(f"Object not found: {value}", error=True)
                return None
        
        return objs
    
    def _on_create_constraint(self):
        """Create the relative matrix constraint."""
        objs = self._validate_objects()
        if not objs:
            return
        
        # Check if target already has connections
        target = objs['target_driven']
        attrs_to_check = ['translate', 'rotate', 'tx', 'ty', 'tz', 'rx', 'ry', 'rz']
        has_connections = False
        for attr in attrs_to_check:
            if cmds.listConnections(f"{target}.{attr}", source=True, destination=False):
                has_connections = True
                break
        
        if has_connections:
            result = QtWidgets.QMessageBox.question(
                self, 
                "Existing Connections",
                f"{target} already has incoming connections.\nRemove them and create new constraint?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
            )
            if result != QtWidgets.QMessageBox.Yes:
                return
            remove_connections_from_target(target)
        
        # Create the constraint
        try:
            network_node = create_relative_matrix_constraint(
                objs['source_ref'],
                objs['source_driven'],
                objs['target_ref'],
                objs['target_driven']
            )
            self._set_status("Constraint created successfully", success=True)
            self._refresh_constraint_list()
            
        except Exception as e:
            self._set_status(f"Error: {str(e)}", error=True)
    
    def _on_verify(self):
        """Verify the constraint at current frame using selected list item or field values."""
        # Try to get objects from selection first, then from fields
        selected_items = self.constraints_list.selectedItems()
        
        if selected_items:
            # Use first selected constraint
            item = selected_items[0]
            item_widget = self.constraints_list.itemWidget(item)
            if item_widget:
                data = get_constraint_data(item_widget.network_node)
                objs = {
                    'source_ref': data['source_ref'],
                    'source_driven': data['source_driven'],
                    'target_ref': data['target_ref'],
                    'target_driven': data['target_driven']
                }
            else:
                objs = self._get_objects()
        else:
            objs = self._get_objects()
        
        # Validate
        for key, value in objs.items():
            if not value or not cmds.objExists(value):
                self._set_status(f"Cannot verify: {key} invalid", error=True)
                return
        
        passed, diff = self._verify_constraint(
            objs['source_ref'],
            objs['source_driven'],
            objs['target_ref'],
            objs['target_driven']
        )
        
        if passed:
            self._set_status(f"PASS - Matrix diff: {diff:.6f}", success=True)
        else:
            self._set_status(f"FAIL - Matrix diff: {diff:.6f}", error=True)
    
    def _verify_constraint(self, source_ref, source_driven, target_ref, target_driven, tolerance=0.001):
        """Verify relative offsets match."""
        def get_world_matrix(obj):
            sel = om2.MSelectionList()
            sel.add(obj)
            return om2.MFnTransform(sel.getDagPath(0)).transformation().asMatrix()
        
        def get_local_offset(ref_obj, driven_obj):
            ref_matrix = get_world_matrix(ref_obj)
            driven_matrix = get_world_matrix(driven_obj)
            return driven_matrix * ref_matrix.inverse()
        
        source_offset = get_local_offset(source_ref, source_driven)
        target_offset = get_local_offset(target_ref, target_driven)
        
        max_diff = 0.0
        for i in range(4):
            for j in range(4):
                diff = abs(source_offset.getElement(i, j) - target_offset.getElement(i, j))
                max_diff = max(max_diff, diff)
        
        return max_diff < tolerance, max_diff
    
    def _refresh_constraint_list(self):
        """Refresh the list of existing constraints from the scene."""
        self.constraints_list.clear()
        
        network_nodes = find_constraint_network_nodes()
        
        for network_node in network_nodes:
            data = get_constraint_data(network_node)
            
            # Create list item
            item = QtWidgets.QListWidgetItem()
            item.setSizeHint(QtCore.QSize(0, 30))
            
            # Create custom widget
            display_name = data['display_name'] or network_node
            item_widget = ConstraintListItem(network_node, display_name)
            item_widget.bake_clicked.connect(self._on_bake_single)
            
            self.constraints_list.addItem(item)
            self.constraints_list.setItemWidget(item, item_widget)
        
        count = len(network_nodes)
        if count == 0:
            self._set_status("No constraints found in scene")
        else:
            self._set_status(f"Found {count} constraint(s)")
    
    def _on_selection_changed(self):
        """Handle list selection change - populate fields and select driven control in Maya."""
        selected_items = self.constraints_list.selectedItems()
        if not selected_items:
            return
        
        # Collect all target_driven objects from selected items
        targets_to_select = []
        
        for item in selected_items:
            item_widget = self.constraints_list.itemWidget(item)
            if item_widget:
                data = get_constraint_data(item_widget.network_node)
                if data['target_driven'] and cmds.objExists(data['target_driven']):
                    targets_to_select.append(data['target_driven'])
        
        # Select the driven controls in Maya
        if targets_to_select:
            cmds.select(targets_to_select, replace=True)
        
        # Populate fields from first selected item (for reference)
        first_item = selected_items[0]
        first_widget = self.constraints_list.itemWidget(first_item)
        if first_widget:
            data = get_constraint_data(first_widget.network_node)
            self.source_ref_field.setText(data['source_ref'] or '')
            self.source_driven_field.setText(data['source_driven'] or '')
            self.target_ref_field.setText(data['target_ref'] or '')
            self.target_driven_field.setText(data['target_driven'] or '')
    
    def _on_bake_single(self, network_node):
        """Bake a single constraint."""
        data = get_constraint_data(network_node)
        
        if not data['target_driven'] or not cmds.objExists(data['target_driven']):
            self._set_status(f"Target object not found", error=True)
            return
        
        target = data['target_driven']
        start = self.start_frame_spin.value()
        end = self.end_frame_spin.value()
        
        self._set_status(f"Baking {target}...")
        QtWidgets.QApplication.processEvents()
        
        try:
            _bake_with_viewport_off(target, start, end)
            
            # Delete the constraint (network node + matrix nodes)
            delete_constraint(network_node)
            
            self._set_status(f"Baked {target} and removed constraint", success=True)
            self._refresh_constraint_list()
            
        except Exception as e:
            self._set_status(f"Bake error: {str(e)}", error=True)
    
    def _on_bake_all(self):
        """Bake all constraints in the list."""
        network_nodes = find_constraint_network_nodes()
        
        if not network_nodes:
            self._set_status("No constraints to bake", error=True)
            return
        
        start = self.start_frame_spin.value()
        end = self.end_frame_spin.value()
        
        self._set_status(f"Baking {len(network_nodes)} constraint(s)...")
        QtWidgets.QApplication.processEvents()
        
        baked_count = 0
        
        for network_node in network_nodes:
            data = get_constraint_data(network_node)
            target = data['target_driven']
            
            if not target or not cmds.objExists(target):
                continue
            
            try:
                _bake_with_viewport_off(target, start, end)
                delete_constraint(network_node)
                baked_count += 1
            except Exception as e:
                print(f"Error baking {target}: {e}")
        
        self._set_status(f"Baked {baked_count} constraint(s)", success=True)
        self._refresh_constraint_list()
    
    def _on_delete_selected(self):
        """Delete selected constraints without baking."""
        selected_items = self.constraints_list.selectedItems()
        
        if not selected_items:
            self._set_status("No constraints selected", error=True)
            return
        
        # Confirm deletion
        result = QtWidgets.QMessageBox.question(
            self,
            "Delete Constraints",
            f"Delete {len(selected_items)} constraint(s) without baking?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        
        if result != QtWidgets.QMessageBox.Yes:
            return
        
        for item in selected_items:
            item_widget = self.constraints_list.itemWidget(item)
            if item_widget:
                delete_constraint(item_widget.network_node)
        
        self._set_status(f"Deleted {len(selected_items)} constraint(s)")
        self._refresh_constraint_list()
    
    def _set_status(self, message, error=False, success=False):
        """Update the status label."""
        if error:
            self.status_label.setStyleSheet("color: #ff6b6b; font-style: italic;")
        elif success:
            self.status_label.setStyleSheet("color: #69db7c; font-style: italic;")
        else:
            self.status_label.setStyleSheet("color: gray; font-style: italic;")
        
        self.status_label.setText(message)


# =============================================================================
# Launch function
# =============================================================================

_ui_instance = None

def show():
    """Show the Relative Matrix Constraint UI."""
    global _ui_instance
    
    # Close existing instance
    if _ui_instance is not None:
        try:
            _ui_instance.close()
            _ui_instance.deleteLater()
        except:
            pass
    
    _ui_instance = RelativeMatrixConstraintUI()
    _ui_instance.show()
    return _ui_instance


if __name__ == "__main__":
    show()
