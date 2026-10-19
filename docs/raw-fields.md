# Raw database fields

Every project object has `getProperty(name)` / `setProperty(name, value)`. `name` is the column
in the project database. The installed report templates (`BOMTemplate/*.xml`) select these
columns by name, which is how they were found. `probe_object_fields` lists the ones a live object
answers to; the table below is one sweep on SOLIDWORKS Electrical 2025 SP5 (one object of each
kind). A name not listed may still exist: the probe only tries names built from template columns.

Writing past the typed setters is how `move_folio`, `move_folder` and `set_book_order` work:
`fil_bun_id` / `fil_fol_id` / `fil_tagposition` move an inserted page (the typed setters return
EW_BAD_INPUTS), `bun_tagposition` orders the books. User data is `*_use_data0..2`, which the typed
`setUserData(n)` reaches with n = 0..2.

| Kind | Fields |
|---|---|
| file | `fil_bun_id`, `fil_com_id`, `fil_filename`, `fil_fol_id`, `fil_fun_id`, `fil_id`, `fil_loc_id`, `fil_orderno`, `fil_revisionno`, `fil_tagposition`, `fil_title`, `fil_use_data0`, `fil_use_data1`, `fil_use_data2` |
| book | `bun_id`, `bun_orderno`, `bun_revisionno`, `bun_tag`, `bun_tagposition`, `bun_use_data0`, `bun_use_data1`, `bun_use_data2` |
| folder | `fol_bun_id`, `fol_fol_id`, `fol_id`, `fol_name`, `fol_orderno`, `fol_tagposition`, `fol_use_data0`, `fol_use_data1`, `fol_use_data2` |
| component | `com_com_id`, `com_fun_id`, `com_id`, `com_loc_id`, `com_parenttagpath`, `com_tag`, `com_tagmanual`, `com_tagorderno`, `com_tagpath`, `com_tagposition`, `com_tagroot`, `com_type`, `com_use_data0`, `com_use_data1`, `com_use_data2`, `com_value1`, `com_value2`, `com_value3`, `com_value4`, `com_value5`, `com_value6`, `com_value7` |
| cable | `cab_articlename`, `cab_color`, `cab_diameter`, `cab_ductpath`, `cab_family`, `cab_fun_id`, `cab_gauge`, `cab_har_id`, `cab_id`, `cab_length`, `cab_loc_id_from`, `cab_loc_id_to`, `cab_manufacturer`, `cab_reference`, `cab_section`, `cab_standard`, `cab_stocknumber`, `cab_suppliername`, `cab_swfromcomp`, `cab_swtocomp`, `cab_tag`, `cab_tagmanual`, `cab_tagorderno`, `cab_tagposition`, `cab_tagroot`, `cab_type`, `cab_use_data0`, `cab_use_data1`, `cab_use_data2` |
| wire | `wir_cab_id`, `wir_caw_no`, `wir_cel_idfrom`, `wir_cel_idto`, `wir_color`, `wir_cte_nofrom`, `wir_cte_noto`, `wir_diameter`, `wir_ductpath`, `wir_har_id`, `wir_id`, `wir_length`, `wir_lty_id`, `wir_mes_idmfi`, `wir_orderno`, `wir_sectionorgauge`, `wir_sectiontype`, `wir_signal`, `wir_tag`, `wir_tagmanual`, `wir_tagorderno`, `wir_tagroot`, `wir_type`, `wir_use_data0`, `wir_use_data1`, `wir_use_data2` |
| location | `loc_bun_id`, `loc_id`, `loc_loc_id`, `loc_parenttagpath`, `loc_tagmanual`, `loc_tagorderno`, `loc_tagpath`, `loc_tagposition`, `loc_tagroot`, `loc_text`, `loc_use_data0`, `loc_use_data1`, `loc_use_data2` |
| function | `fun_bun_id`, `fun_fun_id`, `fun_id`, `fun_parenttagpath`, `fun_tagmanual`, `fun_tagorderno`, `fun_tagpath`, `fun_tagposition`, `fun_tagroot`, `fun_text`, `fun_use_data0`, `fun_use_data1`, `fun_use_data2` |
| line | `lin_equ_id`, `lin_fil_id`, `lin_id`, `lin_lty_id`, `lin_mes_id` |
| manufacturer_part | `bom_acces_cel_id`, `bom_acces_cte_no`, `bom_articlename`, `bom_assemblyid`, `bom_excludefrombom`, `bom_har_id`, `bom_id`, `bom_implantx`, `bom_implanty`, `bom_implantz`, `bom_length`, `bom_manufacturer`, `bom_objectid`, `bom_objecttype`, `bom_orderno`, `bom_partname`, `bom_reference`, `bom_stocknumber`, `bom_suppliername`, `bom_use_data0`, `bom_use_data1`, `bom_use_data2`, `bom_value1`, `bom_value2`, `bom_value3`, `bom_value4`, `bom_value5`, `bom_value6`, `bom_value7`, `bom_weight` |
| io | `aio_cel_id`, `aio_channeladdress`, `aio_fun_id`, `aio_id`, `aio_keycode`, `aio_mnemonic`, `aio_use_data0`, `aio_use_data1`, `aio_use_data2` |
