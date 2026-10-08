# -*- coding: utf-8 -*-
"""Package a plot for QField: a folder you copy to a phone and open in the field.

`qgis_plot_workflow.py` produces a desk deliverable. This produces the field
one, and the difference between them is entirely about what happens when there
is no signal, which in Lam Ha is most of the time.

Three things have to be true before a QField project is usable on a farm:

**The imagery has to be in the folder.** A project pointing at an XYZ tile
service renders a grey screen on a phone with no data connection, and it does
it silently, which is the worst way for a tool to fail when you are standing in
somebody's garden. A small GeoTIFF of the plot's own extent is baked in here
instead. It covers the plot and a buffer, nothing more: this is one farm going
offline for a morning, not a basemap library.

**Paths have to be relative.** An absolute `C:\\Users\\...` path resolves to
nothing on Android. The project is written with relative path storage and every
file sits in the same folder.

**There has to be somewhere to put what you see.** A boundary you cannot
annotate is a picture. An observation layer travels with the project, set up
with the widgets QField needs: a date that defaults to today, a dropdown rather
than free text for the category, and a photo field, because a photograph of the
leaf settles arguments that a text box does not.

    "C:/Program Files/QGIS 3.44.14/bin/python-qgis-ltr.bat" \\
        scripts/make_qfield_package.py data/real/qgis/PLOT-01.gpkg

Output goes to data/real/qfield/<PLOT_ID>/, which is gitignored along with
everything else derived from a real household's boundary.

On imagery licensing: Esri World Imagery is used because it needs no key and
QGIS ships it as a default. Caching a single plot for a field visit is ordinary
use. Before any of this is pointed at NKG's portfolio, check what the imagery
licence actually permits at that scale, and expect the answer to be a procured
source rather than a public tile service.
"""

import argparse
import os
import pathlib
import shutil
import sys

from osgeo import gdal, ogr, osr

gdal.UseExceptions()

BUFFER_M = 120          # context around the plot: enough to find the gate
TILE_LEVEL = 18         # Esri has no z19 here; asking deeper returns a grey tile
MAX_PIXELS = 4000       # a phone does not want a gigabyte of basemap

ESRI_IMAGERY = ("https://server.arcgisonline.com/ArcGIS/rest/services/"
                "World_Imagery/MapServer/tile/${z}/${y}/${x}")

# What a field visit actually records. Kept short on purpose: every extra field
# is one more thing to fill in while standing in the sun, and a form nobody
# completes is worse than no form.
OBSERVATION_FIELDS = [
    ("ngay", ogr.OFTDate),
    ("nguoi_ghi", ogr.OFTString),
    ("loai", ogr.OFTString),
    ("mo_ta", ogr.OFTString),
    ("anh", ogr.OFTString),
]

OBSERVATION_KINDS = [
    ("sau_benh", "Sau benh"),
    ("thieu_dinh_duong", "Thieu dinh duong"),
    ("cay_chet", "Cay chet hoac cay yeu"),
    ("ranh_sai", "Ranh gioi khong khop thuc te"),
    ("mia_ghep", "Cay la, nghi bien di hoac ghep"),
    ("khac", "Khac"),
]


def plot_extent_3857(gpkg, layer_name, buffer_m):
    """The plot's bounding box in Web Mercator, padded, for cutting the basemap."""
    ds = ogr.Open(str(gpkg))
    layer = ds.GetLayerByName(layer_name)
    if layer is None:
        sys.exit("No layer %s in %s" % (layer_name, gpkg))
    x0, x1, y0, y1 = layer.GetExtent()
    ds = None

    src = osr.SpatialReference()
    src.ImportFromEPSG(4326)
    src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    dst = osr.SpatialReference()
    dst.ImportFromEPSG(3857)
    dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    tr = osr.CoordinateTransformation(src, dst)

    corners = [tr.TransformPoint(x, y)[:2] for x in (x0, x1) for y in (y0, y1)]
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]

    # Web Mercator metres are stretched by 1/cos(latitude). Padding by the raw
    # buffer would put the real distance out by 2 % at this latitude, which is
    # harmless here but wrong, and wrong in a way that grows with latitude.
    import math
    lat = (y0 + y1) / 2.0
    pad = buffer_m / math.cos(math.radians(lat))
    return min(xs) - pad, max(ys) + pad, max(xs) + pad, min(ys) - pad


def build_offline_basemap(dst_path, bounds_3857, tile_level=TILE_LEVEL):
    """Cut a GeoTIFF of the plot out of the Esri tile service, once, up front.

    GDAL's WMS driver with a TMS minidriver speaks XYZ, so the same tiles QGIS
    would stream are fetched here and written to disk. The result is a few
    hundred pixels square for a plot this size.
    """
    xml = """<GDAL_WMS>
  <Service name="TMS"><ServerUrl>%s</ServerUrl></Service>
  <DataWindow>
    <UpperLeftX>-20037508.34</UpperLeftX>
    <UpperLeftY>20037508.34</UpperLeftY>
    <LowerRightX>20037508.34</LowerRightX>
    <LowerRightY>-20037508.34</LowerRightY>
    <TileLevel>%d</TileLevel>
    <TileCountX>1</TileCountX>
    <TileCountY>1</TileCountY>
    <YOrigin>top</YOrigin>
  </DataWindow>
  <Projection>EPSG:3857</Projection>
  <BlockSizeX>256</BlockSizeX>
  <BlockSizeY>256</BlockSizeY>
  <BandsCount>3</BandsCount>
  <MaxConnections>4</MaxConnections>
  <Cache/>
</GDAL_WMS>""" % (ESRI_IMAGERY, tile_level)

    src = gdal.Open(xml)
    if src is None:
        return None, "GDAL cannot open the tile service"

    ulx, uly, lrx, lry = bounds_3857
    width_px = abs(lrx - ulx) / abs(src.GetGeoTransform()[1])
    if width_px > MAX_PIXELS:
        return None, "extent needs %d px, over the %d px cap" % (width_px, MAX_PIXELS)

    try:
        gdal.Translate(str(dst_path), src, options=gdal.TranslateOptions(
            format="GTiff", projWin=[ulx, uly, lrx, lry],
            creationOptions=["COMPRESS=JPEG", "JPEG_QUALITY=85", "TILED=YES",
                             "PHOTOMETRIC=YCBCR"]))
    except Exception as exc:
        return None, str(exc)
    finally:
        src = None
    return dst_path, None


def create_observation_layer(path):
    """An empty point layer for what you see while walking the plot."""
    if path.exists():
        return False
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

    ds = ogr.GetDriverByName("GPKG").CreateDataSource(str(path))
    layer = ds.CreateLayer("ghi_chep", srs, ogr.wkbPoint)
    for name, ftype in OBSERVATION_FIELDS:
        field = ogr.FieldDefn(name, ftype)
        if ftype == ogr.OFTString:
            field.SetWidth(254)
        layer.CreateField(field)
    ds = None
    return True


def write_project(folder, plot_id, plot_gpkg, obs_gpkg, basemap, epsg):
    """The .qgz itself, with relative paths and QField-ready form widgets."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from qgis.core import (QgsApplication, QgsProject, QgsVectorLayer, QgsFillSymbol,
                           QgsRasterLayer, QgsCoordinateReferenceSystem,
                           QgsEditorWidgetSetup, QgsDefaultValue, QgsMarkerSymbol)

    qgs = QgsApplication([], False)
    qgs.initQgis()
    try:
        project = QgsProject.instance()
        project.clear()
        # Relative, or nothing resolves once the folder is on a phone.
        project.writeEntryBool("Paths", "/Absolute", False)
        project.setCrs(QgsCoordinateReferenceSystem("EPSG:%d" % epsg))
        project.setTitle("%s field survey" % plot_id)

        if basemap is not None:
            raster = QgsRasterLayer(str(basemap), "Anh ve tinh (offline)", "gdal")
            if raster.isValid():
                project.addMapLayer(raster)

        for layer_name, label, stroke, width in (
                ("boundary_full", "Ranh lo day du", "255,193,7", "1.0"),
                ("boundary_simplified", "Ranh lo rut gon", "211,47,47", "0.6")):
            layer = QgsVectorLayer("%s|layername=%s" % (plot_gpkg, layer_name),
                                   label, "ogr")
            if not layer.isValid():
                continue
            layer.renderer().setSymbol(QgsFillSymbol.createSimple({
                "style": "no", "outline_color": stroke,
                "outline_width": width, "outline_width_unit": "MM"}))
            layer.setReadOnly(True)        # the boundary is evidence, not a sketch pad
            project.addMapLayer(layer)

        obs = QgsVectorLayer("%s|layername=ghi_chep" % obs_gpkg, "Ghi chep ngoai vuon", "ogr")
        if obs.isValid():
            obs.renderer().setSymbol(QgsMarkerSymbol.createSimple({
                "name": "circle", "color": "0,188,212", "size": "3.4",
                "outline_color": "255,255,255", "outline_width": "0.6"}))
            _configure_form(obs, QgsEditorWidgetSetup, QgsDefaultValue)
            project.addMapLayer(obs)

        dst = folder / ("%s_field.qgz" % plot_id)
        project.write(str(dst))
        return dst
    finally:
        qgs.exitQgis()


def _configure_form(layer, QgsEditorWidgetSetup, QgsDefaultValue):
    """Widgets, so the form can be filled with a thumb and not a keyboard."""
    idx = {f.name(): i for i, f in enumerate(layer.fields())}

    layer.setEditorWidgetSetup(idx["ngay"], QgsEditorWidgetSetup("DateTime", {
        "display_format": "yyyy-MM-dd", "field_format": "yyyy-MM-dd",
        "calendar_popup": True, "allow_null": False}))
    layer.setDefaultValueDefinition(idx["ngay"], QgsDefaultValue("format_date(now(),'yyyy-MM-dd')"))

    # A dropdown, not free text. Free text makes every record unique and so
    # makes the whole layer impossible to count, which is how a field log
    # quietly becomes write-only.
    layer.setEditorWidgetSetup(idx["loai"], QgsEditorWidgetSetup("ValueMap", {
        "map": [{label: value} for value, label in OBSERVATION_KINDS]}))

    layer.setEditorWidgetSetup(idx["mo_ta"], QgsEditorWidgetSetup("TextEdit",
                                                                 {"IsMultiline": True}))
    layer.setEditorWidgetSetup(idx["anh"], QgsEditorWidgetSetup("ExternalResource", {
        "RelativeStorage": 1,                 # relative to the project folder
        "DefaultRoot": "DCIM",
        "StorageMode": 0,
        "DocumentViewer": 1,                  # show the photo in the form
        "DocumentViewerHeight": 0,
        "DocumentViewerWidth": 0,
        "FileWidget": True,
        "FileWidgetButton": True}))

    for name in ("ngay", "nguoi_ghi", "loai"):
        layer.setFieldConstraint(idx[name], layer.fields().at(idx[name]).ConstraintNotNull
                                 if hasattr(layer.fields().at(idx[name]), "ConstraintNotNull")
                                 else 1)
    layer.setDisplayExpression('coalesce("loai", \'ghi chep\')')


HOWTO = """HUONG DAN DUNG TREN DIEN THOAI
================================

Thu muc nay chay offline. Khong can song.

1. CAI
   Cai QField tu Google Play (mien phi, cua OPENGIS.ch).

2. CHEP SANG DIEN THOAI
   Noi day USB, chep NGUYEN CA THU MUC nay vao dien thoai.
   Cho de tim: bo nho trong > Documents > QField.
   Phai chep ca thu muc, khong chep rieng file .qgz.
   Thieu mot file la ban do mat mot lop.

3. MO
   Mo QField > Open local file > tim den thu muc > chon file *_field.qgz

4. NGOAI VUON
   - Bam nut dinh vi (goc duoi phai) de xem minh dang dung o dau so voi ranh.
   - Ranh vang la duong di bo do that, ranh do la ban rut gon de nop.
   - Them ghi chep: bat nut but chi (Edit), chon lop "Ghi chep ngoai vuon",
     bam vao vi tri tren ban do, dien form.
   - Ngay tu dien san. Loai chon tu danh sach. Anh bam vao o "anh" roi chup.

5. MANG VE
   Chep nguoc ca thu muc tu dien thoai ve may tinh.
   File ghi_chep.gpkg la noi moi ghi chep nam o do.
   Anh nam trong thu muc DCIM ben trong thu muc nay.

VIEC CAN LAM NGAY LAN DAU RA VUON
---------------------------------
Dung o GOC LO, xem cham dinh vi cua minh cach duong ranh bao nhieu met.
Do la sai so that cua ca quy trinh: may do + GPS dien thoai cong lai.
Ghi con so do lai. Khong co con so do thi moi dien tich deu la loi noi suong.

Kiem them:
- Ranh co di dung qua hang cay ngoai cung khong?
- Cho nao ranh cat vao vuon hang xom?
- Cai "gai" o canh trai (chi tiet trong file metadata) la that hay la loi GPS?
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("plot_gpkg", help="the GeoPackage from qgis_plot_workflow.py")
    ap.add_argument("--id", default=None, help="plot id (default: gpkg stem)")
    ap.add_argument("--outdir", default=None,
                    help="default: <gpkg dir>/../qfield/<id>")
    ap.add_argument("--epsg", type=int, default=3406,
                    help="project CRS (default 3406, VN-2000 UTM 49N)")
    ap.add_argument("--buffer", type=float, default=BUFFER_M,
                    help="metres of imagery around the plot (default %d)" % BUFFER_M)
    ap.add_argument("--no-basemap", action="store_true",
                    help="skip the imagery download and build the project without it")
    args = ap.parse_args(argv)

    src = pathlib.Path(args.plot_gpkg).resolve()
    if not src.exists():
        sys.exit("No such file: %s" % src)
    plot_id = args.id or src.stem
    folder = pathlib.Path(args.outdir) if args.outdir \
        else src.parent.parent / "qfield" / plot_id
    folder.mkdir(parents=True, exist_ok=True)

    print("Packaging %s for QField" % plot_id)
    print("  -> %s" % folder)

    print("1. BOUNDARY")
    plot_copy = folder / src.name
    shutil.copy2(src, plot_copy)
    print("   copied %s" % src.name)

    print("2. OBSERVATION LAYER")
    obs = folder / "ghi_chep.gpkg"
    if create_observation_layer(obs):
        print("   created ghi_chep.gpkg (empty, %d fields)" % len(OBSERVATION_FIELDS))
    else:
        print("   ghi_chep.gpkg already there, kept (it may hold field records)")

    print("3. OFFLINE IMAGERY")
    basemap = None
    if args.no_basemap:
        print("   skipped")
    else:
        bounds = plot_extent_3857(plot_copy, "boundary_full", args.buffer)
        target = folder / ("%s_imagery.tif" % plot_id)
        basemap, err = build_offline_basemap(target, bounds)
        if basemap is None:
            print("   ! no basemap: %s" % err)
            print("     The project still works, it just opens on a blank canvas.")
        else:
            ds = gdal.Open(str(basemap))
            print("   %s, %d x %d px, %.1f KB, buffer %.0f m"
                  % (basemap.name, ds.RasterXSize, ds.RasterYSize,
                     basemap.stat().st_size / 1024.0, args.buffer))
            ds = None

    print("4. PROJECT")
    qgz = write_project(folder, plot_id, str(plot_copy), str(obs), basemap, args.epsg)
    print("   %s (relative paths)" % qgz.name)

    print("5. INSTRUCTIONS")
    howto = folder / "DOC_TRUOC_KHI_RA_VUON.txt"
    howto.write_text(HOWTO, encoding="utf-8")
    print("   %s" % howto.name)

    (folder / "DCIM").mkdir(exist_ok=True)

    total = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
    print("\nFolder is %.1f MB. Copy the whole thing to the phone." % (total / 1048576.0))
    for f in sorted(folder.rglob("*")):
        if f.is_file():
            print("   %-34s %8.1f KB" % (f.relative_to(folder), f.stat().st_size / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
