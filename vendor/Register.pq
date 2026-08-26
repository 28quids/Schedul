// =============================================================================
// Register.pq  -  builds the central schedule register
//
// Reads the Metadata sheet out of every schedule file in a folder and pivots
// it into one row per schedule. Read-only, native Excel, no VBA.
//
// To install:
//   1. Open MAINPROJECTINFO.xlsx
//   2. Data > Get Data > Launch Power Query Editor
//   3. Home > New Source > Blank Query
//   4. Home > Advanced Editor, paste all of this, replacing what's there
//   5. Edit the SchedulesFolder line below to your actual path
//   6. Rename the query to "Register", then Close & Load To > Existing sheet,
//      ScheduleList!$A$1
//   7. From then on: Data > Refresh All
//
// It relies on each schedule file having a sheet called "Metadata" laid out as
// two columns headed Column1 / Column2. That is exactly the house format.
// =============================================================================

let
    // ---- EDIT THIS LINE ------------------------------------------------------
    SchedulesFolder = "C:\SharePoint\Project\Schedules\schedules",
    // -------------------------------------------------------------------------

    Source = Folder.Files(SchedulesFolder),

    // Only real schedule workbooks: skip Excel lock files, frozen issue copies,
    // and the central workbooks themselves.
    OnlySchedules = Table.SelectRows(
        Source,
        each Text.EndsWith([Extension], ".xlsx", Comparer.OrdinalIgnoreCase)
             and not Text.StartsWith([Name], "~$")
             and not Text.Contains([Name], "_ISSUED")
             and not Text.Contains([Name], "MAINPROJECTINFO")
             and not Text.Contains([Name], "EQUIPMENT_LIBRARY")
    ),

    // Pull the Metadata sheet out of each file. Files without one are dropped
    // rather than failing the whole refresh.
    AddMeta = Table.AddColumn(OnlySchedules, "Meta", each
        try
            let
                book  = Excel.Workbook([Content], null, true),
                sheet = Table.SelectRows(book, each [Item] = "Metadata" and [Kind] = "Sheet"){0}[Data],
                promoted = Table.PromoteHeaders(sheet, [PromoteAllScalars = true]),
                typed = Table.TransformColumnTypes(promoted, {{"Column1", type text}})
            in
                typed
        otherwise null
    ),

    Valid = Table.SelectRows(AddMeta, each [Meta] <> null),

    Expanded = Table.ExpandTableColumn(
        Valid, "Meta", {"Column1", "Column2"}, {"Key", "Value"}),

    Kept = Table.SelectRows(Expanded, each [Key] <> null and [Key] <> ""),

    Trimmed = Table.SelectColumns(Kept, {"Name", "Key", "Value"}),

    Pivoted = Table.Pivot(
        Trimmed, List.Distinct(Trimmed[Key]), "Key", "Value"),

    // Guard against a schedule file missing one of the keys
    Ensured = Table.SelectColumns(
        Pivoted,
        {"DocumentNumber", "ScheduleName", "Revision", "IssueDate",
         "Status", "StatusDescription", "EquipmentCode", "Name"},
        MissingField.UseNull),

    Renamed = Table.RenameColumns(Ensured, {{"Name", "FileName"}}),

    Typed = Table.TransformColumnTypes(Renamed, {
        {"DocumentNumber", type text},
        {"ScheduleName", type text},
        {"Revision", type text},
        {"IssueDate", type date},
        {"Status", type text},
        {"StatusDescription", type text},
        {"EquipmentCode", type text},
        {"FileName", type text}
    }),

    // Flag anything still at work-in-progress so the register doubles as a
    // pre-issue check.
    WithFlag = Table.AddColumn(Typed, "Ready To Issue", each
        if [Status] = null then "NO STATUS"
        else if [Status] = "S0" then "WIP"
        else "Yes", type text),

    Sorted = Table.Sort(WithFlag, {{"DocumentNumber", Order.Ascending}})
in
    Sorted
