import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.ops import linemerge
from shapely.geometry import Point
from shapely.validation import make_valid
from pygeos import multipolygons

import momepy as mm # outp

def _polygonize_ifnone(edges, polys):
    if polys is None:
        pre_polys = polygonize(edges.geometry)
        polys = gpd.GeoDataFrame(geometry=[g for g in pre_polys], crs=edges.crs)
    return polys


def _selecting_rabs_from_poly(
    gdf, circom_threshold=0.7, area_threshold=0.85, include_adjacent=True
):
    """
    From a GeoDataFrame of polygons, returns a GDF of polygons that are
    above the Circular Compaactness threshold.

    Return
    ________
    GeoDataFrames : round abouts and adjacent polygons
    """
    # calculate parameters
    gdf = gdf.copy()
    gdf["area"] = gdf.geometry.area
    gdf["circom"] = CircularCompactness(gdf, "area").series

    # selecting round about polygons based on compactness
    rab = gdf[gdf.circom > circom_threshold]
    # exclude those above the area threshold
    area_threshold_val = gdf.area.quantile(area_threshold)
    rab = rab[rab.area < area_threshold_val]

    if include_adjacent is True:
        # calculating some parameters
        bounds = rab.geometry.bounds
        rab = pd.concat([rab, bounds], axis=1)
        rab["deltax"] = rab.maxx - rab.minx
        rab["deltay"] = rab.maxy - rab.miny
        rab["rab_diameter"] = rab[["deltax", "deltay"]].max(axis=1)

        # selecting the adjacent areas that are of smaller than itself
        if GPD_10:
            rab_adj = gpd.sjoin(gdf, rab, predicate="intersects")
        else:
            rab_adj = gpd.sjoin(gdf, rab, op="intersects")
        rab_adj = rab_adj[rab_adj.area_right >= rab_adj.area_left]
        rab_adj.index.name = "index"
        rab_adj["hdist"] = 0

        # adding a hausdorff_distance threshold
        # TODO: (should be a way to verctorize)
        for i, group in rab_adj.groupby("index_right"):
            for g in group.itertuples():
                hdist = g.geometry.hausdorff_distance(rab.loc[i].geometry)
                rab_adj.hdist.loc[g.Index] = hdist

        rab_plus = rab_adj[rab_adj.hdist < rab_adj.rab_diameter]

    else:
        rab["index_right"] = rab.index
        rab_plus = rab

    # only keeping relevant fields
    geom_col = rab_plus.geometry.name
    rab_plus = rab_plus[[geom_col, "index_right"]]

    return rab_plus


def _rabs_center_points(gdf, center_type="centroid"):
    """
    From a selection of roundabouts, returns an aggregated GeoDataFrame
    per round about with extra column with center_type.
    """
    # creating a multipolygon per RAB (as opposed to dissolving) of the entire
    # composition of the RAB
    # temporary DataFrame where geometry is the array of pygeos geometries
    tmp = pd.DataFrame(gdf.copy())  # temporary hack until shapely 2.0 is out
    tmp["geometry"] = tmp.geometry.values.data

    pygeos_geoms = (
        tmp.groupby("index_right")
        .geometry.apply(pygeos.multipolygons)
        .rename("geometry")
    )
    pygeos_geoms = pygeos.make_valid(pygeos_geoms)

    rab_multipolygons = gpd.GeoDataFrame(pygeos_geoms, crs=gdf.crs)
    # make_valid is transforming the multipolygons into geometry collections because of
    # shared edges

    if center_type == "centroid":
        # geometry centroid of the actual circle
        rab_multipolygons["center_pt"] = gdf[
            gdf.index == gdf.index_right
        ].geometry.centroid

    elif center_type == "mean":
        coords, idxs = pygeos.get_coordinates(pygeos_geoms, return_index=True)
        means = {}
        for i in np.unique(idxs):
            tmps = coords[idxs == i]
            target_idx = rab_multipolygons.index[i]
            means[target_idx] = Point(tmps.mean(axis=0))

        rab_multipolygons["center_pt"] = gpd.GeoSeries(means, crs=gdf.crs)

    # centerpoint of minimum_bounding_circle
    # minimun_bounding_circle() should be available in Shapely 2.0. Implementation still
    # pending.
    # current environment has 1.8.2

    return rab_multipolygons


def _coins_filtering_many_incoming(incoming_many, angle_threshold=0):
    """
    Used only for the cases when more than one incoming line touches the
    roundabout.
    """
    coins_filter_result = []
    # For each new connection, evaluate COINS and select the group from which the new
    # line belongs
    # TODO ideally use the groupby object on line_wkt used earlier
    for g, x in incoming_many.groupby("line_wkt"):
        gs = gpd.GeoSeries(pd.concat([x.geometry, x.line]), crs=incoming_many.crs)
        gdf = gpd.GeoDataFrame(geometry=gs)
        gdf.drop_duplicates(inplace=True)

        coins = COINS(gdf, angle_threshold=angle_threshold)
        # coins.stroke_attribute()) # the groups here don't match the stroke_group in
        # .stroke_gdf()
        stroke_gdf = coins.stroke_gdf()
        if GPD_10:
            orig_geom_join = gpd.sjoin(
                stroke_gdf, gpd.GeoDataFrame(geometry=x.line), predicate="covers"
            )
        else:
            orig_geom_join = gpd.sjoin(
                stroke_gdf, gpd.GeoDataFrame(geometry=x.line), op="covers"
            )
        orig_geom = gpd.GeoSeries(
            [orig_geom_join.geometry.iloc[0]], crs=incoming_many.crs
        )
        gs2 = gpd.GeoDataFrame(geometry=orig_geom)

        gs1 = gpd.GeoSeries(x.geometry, crs=incoming_many.crs)
        gs1 = gpd.GeoDataFrame(geometry=gs1)

        # select the the line that's covered by the joined line returned by COINS
        # one could consider using pygeos shared_paths(a, b) # TODO
        if GPD_10:
            result_idx = gpd.sjoin(gs1, gs2, predicate="covered_by").index
        else:
            result_idx = gpd.sjoin(gs1, gs2, op="covered_by").index
        coins_filter_result.extend(result_idx)

    incoming_many_reduced = incoming_many.loc[coins_filter_result]

    return incoming_many_reduced


def _selecting_incoming_lines(rab_multipolygons, edges, angle_threshold=0):
    """Selecting only the lines that are touching but not covered by
    the ``rab_plus``.
    If more than one LineString is incoming to ``rab_plus``, COINS algorithm
    is used to select the line to be extended further.
    """
    # selecting the lines that are touching but not covered by
    if GPD_10:
        touching = gpd.sjoin(edges, rab_multipolygons, predicate="touches")
    else:
        touching = gpd.sjoin(edges, rab_multipolygons, op="touches")

    if GPD_10:
        idx_drop = gpd.sjoin(edges, rab_multipolygons, predicate="covered_by").index
    else:
        idx_drop = gpd.sjoin(edges, rab_multipolygons, op="covered_by").index

    touching_idx = touching.index
    ls = list(set(touching_idx) - set(idx_drop))

    incoming = touching.loc[ls]

    # figuring out which ends of incoming edges needs to be connected to the center_pt
    incoming["first_pt"] = incoming.geometry.apply(lambda x: Point(x.coords[0]))
    incoming["dist_fisrt_pt"] = incoming.center_pt.distance(incoming.first_pt)
    incoming["last_pt"] = incoming.geometry.apply(lambda x: Point(x.coords[-1]))
    incoming["dist_last_pt"] = incoming.center_pt.distance(incoming.last_pt)
    lines = []
    for i, row in incoming.iterrows():
        if row.dist_fisrt_pt < row.dist_last_pt:
            lines.append(LineString([row.first_pt, row.center_pt]))
        else:
            lines.append(LineString([row.last_pt, row.center_pt]))
    incoming["line"] = gpd.GeoSeries(lines, index=incoming.index, crs=edges.crs)

    # checking in there are more than one incoming lines arriving to the same point
    # which would create several new lines
    incoming["line_wkt"] = incoming.line.to_wkt()
    grouped_lines = incoming.groupby(["line_wkt"])["line_wkt"]
    count_s = grouped_lines.count()

    # separating the incoming roads that come on their own to those that come in groups
    filter_count_one = pd.DataFrame(count_s[count_s == 1])
    filter_count_many = pd.DataFrame(count_s[count_s > 1])
    incoming_ones = pd.merge(
        incoming, filter_count_one, left_on="line_wkt", right_index=True, how="inner"
    )
    incoming_many = pd.merge(
        incoming, filter_count_many, left_on="line_wkt", right_index=True, how="inner"
    )
    incoming_many_reduced = _coins_filtering_many_incoming(
        incoming_many, angle_threshold=angle_threshold
    )

    incoming_all = gpd.GeoDataFrame(
        pd.concat([incoming_ones, incoming_many_reduced]), crs=edges.crs
    )

    return incoming_all, idx_drop


def _ext_lines_to_center(edges, incoming_all, idx_out):
    """
    Extends the Linestrings geometrie to the centerpoint defined by
    _rabs_center_points. Also deleted the lines that originally defined the roundabout.

    Returns
    -------
    GeoDataFrame
        GeoDataFrame of with updated geometry
    """
    # this can most likely be vectorized with pygeos.line_merge()!! #TODO
    incoming_all["geometry"] = incoming_all.apply(
        lambda row: linemerge([row.geometry, row.line]), axis=1
    )

    # deleting the original round about edges
    new_edges = edges.drop(idx_out, axis=0)

    # mantianing the same gdf shape that the original
    incoming_all = incoming_all[edges.columns]
    new_edges = pd.concat([new_edges, incoming_all])

    return new_edges


def roundabout_simplification(
    edges,
    polys=None,
    circom_threshold=0.7,
    area_threshold=0.85,
    include_adjacent=True,
    center_type="centroid",
    angle_threshold=0,
):
    """
    Selects the roundabouts from ``polys`` to create a center point to merge all
    incoming edges. If None is passed, the function will perform shapely polygonization.

    All ``edges`` attributes are preserved and roundabouts are deleted.
    Note that some attributes, like length, may no longer reflect the reality of newly
    constructed geometry.

    If ``include_adjacent`` is True, adjacent polygons to the actual roundabout are
    also selected for simplification if two conditions are met:
        - the area of adjacent polygons is less than the actual roundabout
        - adjacent polygons do not extend beyond the diameter of the actual roundabout.
        This uses hausdorff_distance algorithm.

    Parameters
    ----------
    edges : GeoDataFrame
        GeoDataFrame containing LineString geometry of urban network
    polys : GeoDataFrame
        GeoDataFrame containing Polygon geometry derived from polygonyzing
        ``edges`` GeoDataFrame.
    circom_threshold : float (default 0.7)
        Circular compactness threshold to select roundabouts from ``polys``
        GeoDataFrame.
        Polygons with a higher or equal threshold value will be considered for
        simplification.
    area_threshold : float (default 0.85)
        Percentile threshold value from the area of ``polys`` to leave as input
        geometry.
        Polygons with a higher or equal threshold will be considered as urban blocks
        not considered
        for simplification.
    include_adjacent : boolean (default True)
        Adjacent polygons to be considered also as part of the simplification.
    center_type : string (default 'centroid')
        Method to use for converging the incoming LineStrings.
        Current list of options available : 'centroid', 'mean'.
        - 'centroid': selects the centroid of the actual roundabout (ignoring adjacent
        geometries)
        - 'mean': calculates the mean coordinates from the points of polygons (including
         adjacent geometries)
    angle_threshold : int, float (default 0)
        The angle threshold for the COINS algorithm. Only used when multiple incoming
        LineStrings
        arrive at the same Point to the roundabout or to the adjacent polygons if set
        as True.
        eg. when two 'edges' touch the roundabout at the same point, COINS algorithm
        will evaluate which of those
        incoming lines should be extended accordinf to their deflection angle.
        Segments will only be considered a part of the same street if the deflection
        angle
        is above the threshold.

    Returns
    -------
    GeoDataFrame
        GeoDataFrame with an updated geometry
    """
    polys = _polygonize_ifnone(edges, polys)
    rab = _selecting_rabs_from_poly(
        polys,
        circom_threshold=circom_threshold,
        area_threshold=area_threshold,
        include_adjacent=include_adjacent,
    )
    rab_multipolygons = _rabs_center_points(rab, center_type=center_type)
    incoming_all, idx_drop = _selecting_incoming_lines(
        rab_multipolygons, edges, angle_threshold=angle_threshold
    )
    output = _ext_lines_to_center(edges, incoming_all, idx_drop)

    return output
