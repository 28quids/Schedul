Attribute VB_Name = "MEPSchedules"
'==============================================================================
' MEP Schedule Tools
'
' Load this as an ADD-IN (.xlam), not by importing into each schedule.
' That keeps every schedule file a plain .xlsx with no macro-enabled
' attachments circulating.
'
' Setup:
'   1. New blank workbook > Alt+F11 > File > Import File > MEPSchedules.bas
'   2. Save As > Excel Add-In (*.xlam) to a trusted location
'   3. File > Options > Add-ins > Go > tick it
'   4. Add the macros to the Quick Access Toolbar or a custom ribbon tab
'
' Every macro reads its paths from the hidden Config sheet of the active
' schedule file, so nothing is hardcoded here.
'==============================================================================

Option Explicit

Private Const SH_CONFIG As String = "Config"
Private Const SH_LIB As String = "Library"
Private Const SH_SCHED As String = "Schedule"
Private Const SH_META As String = "Metadata"
Private Const SH_REV As String = "Revision page"
Private Const SH_COVER As String = "Front Cover"

Private Const HDR_ROW As Long = 4       ' field name row on Schedule
Private Const UNIT_ROW As Long = 5      ' unit row on Schedule
Private Const DATA_ROW As Long = 6      ' first data row on Schedule
Private Const LIB_TOP As Long = 3       ' first data row on Library

'------------------------------------------------------------------ utils ---
Private Function Cfg(ByVal key As String) As String
    Dim ws As Worksheet, f As Range
    On Error Resume Next
    Set ws = ActiveWorkbook.Worksheets(SH_CONFIG)
    On Error GoTo 0
    If ws Is Nothing Then
        Err.Raise 5001, , "This does not look like a generated schedule file " & _
                          "(no hidden Config sheet)."
    End If
    Set f = ws.Columns(1).Find(What:=key, LookAt:=xlWhole, MatchCase:=True)
    If f Is Nothing Then Err.Raise 5002, , "Config key not found: " & key
    Cfg = CStr(f.Offset(0, 1).Value)
End Function

Private Sub SetCfg(ByVal key As String, ByVal val As Variant)
    Dim f As Range
    Set f = ActiveWorkbook.Worksheets(SH_CONFIG).Columns(1) _
            .Find(What:=key, LookAt:=xlWhole, MatchCase:=True)
    If Not f Is Nothing Then f.Offset(0, 1).Value = val
End Sub

Private Function LastLibRow(ws As Worksheet) As Long
    LastLibRow = ws.Cells(ws.Rows.Count, 1).End(xlUp).Row
    If LastLibRow < LIB_TOP Then LastLibRow = LIB_TOP - 1
End Function

Private Function ModelCol(sc As Worksheet) As Long
    Dim f As Range
    Set f = sc.Rows(HDR_ROW).Find(What:="Model Reference", LookAt:=xlWhole)
    If f Is Nothing Then Err.Raise 5003, , "Could not find the Model Reference column."
    ModelCol = f.Column
End Function

Private Function TypeColCount(ActiveWb As Workbook) As Long
    TypeColCount = ActiveWb.Worksheets(SH_LIB).Cells(1, 1).End(xlToRight).Column - 1
End Function

Private Function JEsc(ByVal s As String) As String
    s = Replace(s, "\", "\\")
    s = Replace(s, """", "\""")
    s = Replace(s, vbCrLf, " ")
    s = Replace(s, vbLf, " ")
    s = Replace(s, vbTab, " ")
    JEsc = s
End Function

' Canonical field name: row 1 header plus the row 2 unit, e.g. "Length (mm)".
' Keeping the unit in the key is what stops the central database going
' ambiguous once several people contribute to it.
Private Function LibHeader(lb As Worksheet, ByVal col As Long) As String
    Dim n As String, u As String
    n = Trim$(CStr(lb.Cells(1, col).Value))
    u = Trim$(CStr(lb.Cells(2, col).Value))
    If Len(u) > 0 Then LibHeader = n & " (" & u & ")" Else LibHeader = n
End Function

Private Function SafeName(ByVal s As String) As String
    Dim i As Long, ch As String, o As String
    For i = 1 To Len(s)
        ch = Mid$(s, i, 1)
        If InStr("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_", ch) > 0 Then
            o = o & ch
        Else
            o = o & "_"
        End If
    Next i
    SafeName = o
End Function

'=========================================================== 1. PROJECT ======
' Pulls Client / Project Name / Project Number from the central
' MAINPROJECTINFO file and writes them into Config AS VALUES.
' No live link is created, so the file stays self-contained.
'=============================================================================
Public Sub RefreshProjectData()
    Dim src As Workbook, ss As Worksheet, p As String
    Dim opened As Boolean, tgt As Workbook

    Set tgt = ActiveWorkbook
    p = Cfg("path_project_info")

    If Dir(p) = "" Then
        MsgBox "Cannot find the central project file:" & vbCrLf & vbCrLf & p & vbCrLf & vbCrLf & _
               "Check the path_project_info row on the Config sheet.", vbExclamation
        Exit Sub
    End If

    Application.ScreenUpdating = False
    On Error GoTo Fail
    Set src = Workbooks.Open(p, ReadOnly:=True, UpdateLinks:=0)
    opened = True
    Set ss = src.Worksheets("Setup")

    tgt.Activate
    SetCfg "Client", ss.Range("B1").Value
    SetCfg "Project Name", ss.Range("B3").Value
    SetCfg "Project Number", ss.Range("B4").Value

    ' any additional key/value rows from row 6 down
    Dim r As Long
    For r = 6 To ss.Cells(ss.Rows.Count, 1).End(xlUp).Row
        If Len(Trim$(CStr(ss.Cells(r, 1).Value))) > 0 Then
            SetCfg CStr(ss.Cells(r, 1).Value), ss.Cells(r, 2).Value
        End If
    Next r

    src.Close SaveChanges:=False
    Application.ScreenUpdating = True
    MsgBox "Project data refreshed from central and written as values.", vbInformation
    Exit Sub
Fail:
    If opened Then On Error Resume Next: src.Close SaveChanges:=False
    Application.ScreenUpdating = True
    MsgBox "Refresh failed: " & Err.Description, vbCritical
End Sub

'=========================================================== 2. LIBRARY ======
' Replaces this file's hidden Library sheet with the current contents of the
' matching sheet in the central equipment library.
'=============================================================================
Public Sub RefreshLibrary()
    Dim src As Workbook, ss As Worksheet, lb As Worksheet
    Dim p As String, code As String, opened As Boolean, n As Long, tgt As Workbook

    Set tgt = ActiveWorkbook
    p = Cfg("path_equipment_library")
    code = Cfg("EquipmentCode")
    Set lb = tgt.Worksheets(SH_LIB)

    If Dir(p) = "" Then
        MsgBox "Cannot find the central library:" & vbCrLf & vbCrLf & p, vbExclamation
        Exit Sub
    End If

    Application.ScreenUpdating = False
    On Error GoTo Fail
    Set src = Workbooks.Open(p, ReadOnly:=True, UpdateLinks:=0)
    opened = True

    On Error Resume Next
    Set ss = src.Worksheets(code)
    On Error GoTo Fail
    If ss Is Nothing Then
        src.Close SaveChanges:=False
        Application.ScreenUpdating = True
        MsgBox "The central library has no sheet named '" & code & "'.", vbExclamation
        Exit Sub
    End If

    n = ss.Cells(ss.Rows.Count, 1).End(xlUp).Row
    lb.Range(lb.Cells(LIB_TOP, 1), lb.Cells(lb.Rows.Count, 200)).ClearContents
    If n >= LIB_TOP Then
        lb.Range(lb.Cells(LIB_TOP, 1), lb.Cells(n, TypeColCount(tgt) + 1)).Value = _
            ss.Range(ss.Cells(LIB_TOP, 1), ss.Cells(n, TypeColCount(tgt) + 1)).Value
    End If

    src.Close SaveChanges:=False
    Application.ScreenUpdating = True
    MsgBox (n - LIB_TOP + 1) & " library entries loaded for " & code & ".", vbInformation
    Exit Sub
Fail:
    If opened Then On Error Resume Next: src.Close SaveChanges:=False
    Application.ScreenUpdating = True
    MsgBox "Library refresh failed: " & Err.Description, vbCritical
End Sub

'========================================================== 3. ADD TYPE ======
' Select any cell on the schedule row you are working on, then run this.
' It clears the INDEX/MATCH formulas across the product columns of that row
' so you can type a new product's data straight into the schedule.
'=============================================================================
Public Sub AddNewType()
    Dim sc As Worksheet, r As Long, mc As Long, nT As Long, rng As Range

    Set sc = ActiveWorkbook.Worksheets(SH_SCHED)
    If ActiveSheet.Name <> SH_SCHED Then
        MsgBox "Select a cell on the Schedule sheet first.", vbExclamation
        Exit Sub
    End If

    r = ActiveCell.Row
    If r < DATA_ROW Then
        MsgBox "Select a cell on a schedule data row (row " & DATA_ROW & " or below).", vbExclamation
        Exit Sub
    End If

    mc = ModelCol(sc)
    nT = TypeColCount(ActiveWorkbook)

    If Len(Trim$(CStr(sc.Cells(r, mc).Value))) = 0 Then
        MsgBox "Type the new Model Reference into the Model Reference cell first, " & _
               "then run this again.", vbExclamation
        Exit Sub
    End If

    Set rng = sc.Range(sc.Cells(r, mc + 1), sc.Cells(r, mc + nT))
    rng.ClearContents
    rng.Font.Color = RGB(0, 0, 255)
    rng.Interior.Color = RGB(255, 255, 204)

    MsgBox "Product columns unlocked on row " & r & "." & vbCrLf & vbCrLf & _
           "Fill them in, then run 'Save Type To Library'.", vbInformation
    sc.Cells(r, mc + 1).Select
End Sub

'====================================================== 4. SAVE TO LIBRARY ===
' Writes the active row's product data to:
'   a) this file's hidden Library sheet, so it works here immediately
'   b) a single JSON file in the central submissions folder, for review
'
' It deliberately does NOT write to the master library. One file per
' submission means no write conflicts and a full audit trail.
'=============================================================================
Public Sub SaveTypeToLibrary()
    Dim sc As Worksheet, lb As Worksheet
    Dim r As Long, mc As Long, nT As Long, i As Long, nr As Long
    Dim modelRef As String, code As String, fldr As String, fn As String
    Dim js As String, ff As Integer, blanks As Long

    If ActiveSheet.Name <> SH_SCHED Then
        MsgBox "Select the schedule row you want to save, on the Schedule sheet.", vbExclamation
        Exit Sub
    End If

    Set sc = ActiveWorkbook.Worksheets(SH_SCHED)
    Set lb = ActiveWorkbook.Worksheets(SH_LIB)
    r = ActiveCell.Row
    If r < DATA_ROW Then MsgBox "Select a data row.", vbExclamation: Exit Sub

    mc = ModelCol(sc)
    nT = TypeColCount(ActiveWorkbook)
    modelRef = Trim$(CStr(sc.Cells(r, mc).Value))

    If Len(modelRef) = 0 Then MsgBox "No Model Reference on this row.", vbExclamation: Exit Sub

    For i = 1 To nT
        If Len(Trim$(CStr(sc.Cells(r, mc + i).Value))) = 0 Then blanks = blanks + 1
    Next i
    If blanks > 0 Then
        If MsgBox(blanks & " of " & nT & " product fields are blank." & vbCrLf & vbCrLf & _
                  "Save anyway?", vbYesNo + vbQuestion) = vbNo Then Exit Sub
    End If

    ' -- a) local library ----------------------------------------------------
    Dim existing As Range
    Set existing = lb.Columns(1).Find(What:=modelRef, LookAt:=xlWhole, MatchCase:=False)
    If existing Is Nothing Then
        nr = LastLibRow(lb) + 1
        If nr < LIB_TOP Then nr = LIB_TOP
    Else
        If MsgBox("'" & modelRef & "' is already in this file's library. Overwrite it?", _
                  vbYesNo + vbQuestion) = vbNo Then Exit Sub
        nr = existing.Row
    End If

    lb.Cells(nr, 1).Value = modelRef
    For i = 1 To nT
        lb.Cells(nr, i + 1).Value = sc.Cells(r, mc + i).Value
    Next i

    ' -- b) submission file --------------------------------------------------
    code = Cfg("EquipmentCode")
    fldr = Cfg("path_submissions_folder")
    If Right$(fldr, 1) <> "\" Then fldr = fldr & "\"

    If Dir(fldr, vbDirectory) = "" Then
        MsgBox "Saved into this file's library, but the submissions folder was not found:" & _
               vbCrLf & vbCrLf & fldr & vbCrLf & vbCrLf & _
               "The entry will not reach the central database until that path is valid.", _
               vbExclamation
        GoTo Restore
    End If

    js = "{" & vbCrLf
    js = js & "  ""equipment_code"": """ & JEsc(code) & """," & vbCrLf
    js = js & "  ""model_reference"": """ & JEsc(modelRef) & """," & vbCrLf
    js = js & "  ""submitted_by"": """ & JEsc(Application.UserName) & """," & vbCrLf
    js = js & "  ""submitted_on"": """ & Format$(Now, "yyyy-mm-dd hh:nn:ss") & """," & vbCrLf
    js = js & "  ""source_document"": """ & JEsc(Cfg("DocumentNumber")) & """," & vbCrLf
    js = js & "  ""fields"": {" & vbCrLf
    For i = 1 To nT
        js = js & "    """ & JEsc(LibHeader(lb, i + 1)) & """: """ & _
             JEsc(CStr(sc.Cells(r, mc + i).Value)) & """"
        If i < nT Then js = js & ","
        js = js & vbCrLf
    Next i
    js = js & "  }" & vbCrLf & "}"

    fn = fldr & code & "_" & SafeName(modelRef) & "_" & _
         SafeName(Application.UserName) & "_" & Format$(Now, "yyyymmdd_hhnnss") & ".json"

    ff = FreeFile
    Open fn For Output As #ff
    Print #ff, js
    Close #ff

Restore:
    ' put the INDEX/MATCH formula back so the row behaves like every other row
    Dim lc As String, f As String
    For i = 1 To nT
        lc = Split(lb.Cells(1, i + 1).Address(True, False), "$")(0)
        f = "=IF($" & Split(sc.Cells(r, mc).Address(True, False), "$")(0) & r & _
            "="""","""",IFERROR(INDEX(Library!$" & lc & "$" & LIB_TOP & ":$" & lc & "$2000," & _
            "MATCH($" & Split(sc.Cells(r, mc).Address(True, False), "$")(0) & r & _
            ",Library!$A$" & LIB_TOP & ":$A$2000,0)),""NOT FOUND""))"
        With sc.Cells(r, mc + i)
            .Formula = f
            .Font.Color = RGB(0, 128, 0)
            .Interior.Pattern = xlNone
        End With
    Next i

    MsgBox "'" & modelRef & "' saved to this file's library" & _
           IIf(Dir(fldr, vbDirectory) <> "", " and submitted for review.", "."), vbInformation
End Sub

'====================================================== 5. FREEZE FOR ISSUE ==
' Saves a VALUES-ONLY copy alongside the working file. Use this for the
' version that goes on the CDE, so the issued numbers can never drift when
' the central library is later corrected.
'=============================================================================
Public Sub FreezeForIssue()
    Dim src As Workbook, cp As Workbook, ws As Worksheet, p As String

    Set src = ActiveWorkbook
    If MsgBox("This creates a values-only copy of this file for issue." & vbCrLf & vbCrLf & _
              "The working file is not changed. Continue?", vbYesNo + vbQuestion) = vbNo Then Exit Sub

    Application.ScreenUpdating = False
    src.Sheets.Copy
    Set cp = ActiveWorkbook

    For Each ws In cp.Worksheets
        ws.Unprotect
        ws.UsedRange.Value = ws.UsedRange.Value
    Next ws

    On Error Resume Next
    cp.Worksheets(SH_CONFIG).Delete
    cp.Worksheets("Lists").Delete
    cp.Worksheets(SH_LIB).Delete
    On Error GoTo 0

    p = src.Path & "\" & Replace(src.Name, ".xlsx", "") & "_ISSUED.xlsx"
    Application.DisplayAlerts = False
    cp.SaveAs p, FileFormat:=51
    Application.DisplayAlerts = True
    Application.ScreenUpdating = True

    MsgBox "Frozen copy saved as:" & vbCrLf & vbCrLf & p, vbInformation
End Sub

'============================================================ 6. PDF =========
Public Sub ExportSchedulePDF()
    Dim wb As Workbook, p As String
    Set wb = ActiveWorkbook
    p = wb.Path & "\" & Cfg("DocumentNumber") & ".pdf"
    wb.Worksheets(Array(SH_COVER, SH_REV, SH_SCHED)).Select
    ActiveSheet.ExportAsFixedFormat Type:=xlTypePDF, Filename:=p, _
        Quality:=xlQualityStandard, IgnorePrintAreas:=False, OpenAfterPublish:=False
    wb.Worksheets(SH_SCHED).Select
    MsgBox "PDF written to:" & vbCrLf & vbCrLf & p, vbInformation
End Sub

' Batch: every schedule .xlsx in a chosen folder, straight to PDF.
Public Sub ExportAllPDFs()
    Dim fd As FileDialog, fldr As String, fn As String
    Dim wb As Workbook, n As Long

    Set fd = Application.FileDialog(msoFileDialogFolderPicker)
    fd.Title = "Select the folder containing the schedule files"
    If fd.Show <> -1 Then Exit Sub
    fldr = fd.SelectedItems(1) & "\"

    Application.ScreenUpdating = False
    Application.DisplayAlerts = False
    fn = Dir(fldr & "*.xlsx")
    Do While Len(fn) > 0
        If InStr(fn, "~$") = 0 And InStr(fn, "_ISSUED") = 0 Then
            On Error Resume Next
            Set wb = Workbooks.Open(fldr & fn, UpdateLinks:=0)
            If Not wb Is Nothing Then
                If Not wb.Worksheets(SH_CONFIG) Is Nothing Then
                    ExportSchedulePDFQuiet wb
                    n = n + 1
                End If
                wb.Close SaveChanges:=False
            End If
            Set wb = Nothing
            On Error GoTo 0
        End If
        fn = Dir
    Loop
    Application.DisplayAlerts = True
    Application.ScreenUpdating = True
    MsgBox n & " schedules exported to PDF.", vbInformation
End Sub

Private Sub ExportSchedulePDFQuiet(wb As Workbook)
    Dim p As String, f As Range
    Set f = wb.Worksheets(SH_CONFIG).Columns(1).Find("DocumentNumber", LookAt:=xlWhole)
    If f Is Nothing Then Exit Sub
    p = wb.Path & "\" & CStr(f.Offset(0, 1).Value) & ".pdf"
    wb.Worksheets(Array(SH_COVER, SH_REV, SH_SCHED)).Select
    wb.ActiveSheet.ExportAsFixedFormat Type:=xlTypePDF, Filename:=p, IgnorePrintAreas:=False
End Sub
