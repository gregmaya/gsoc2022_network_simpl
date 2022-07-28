import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.ops import linemerge
from shapely import geometry
from shapely.validation import make_valid
from pygeos import multipolygons

import momepy as mm # outp

def _selecting_rabs_from_poly(gdf, circom_threshold = 0.7, perc_area_threshold = 0.85, include_adjacent=True) :
    """
    From a GeoDataFrame of polygons, returns a GDF of polygons that are 
    above the Circular Compaactness threshold.
    
    Return
    ________
    GeoDataFrames : round abouts and adjacent polygons
    """
    # calculate parameters
    gdf["area"] = gdf.geometry.area
    gdf["circom"] = mm.CircularCompactness(gdf, "area").series
    
    #selecting round about polygons based on compactness
    rab = gdf[gdf.circom > circom_threshold]
    #exclude those above the area threshold
    area_threshold = gdf.area.quantile(perc_area_threshold)
    rab = rab[rab.area < area_threshold]
    
    if include_adjacent == True :
        #selecting the adjacent areas that are of smaller than itself
        rab_adj = gpd.sjoin(gdf, rab, predicate = 'intersects')
        rab_adj = rab_adj[rab_adj.area_right >= rab_adj.area_left]
        
        adj_long_polys = []
        #iterate through original rabs
        for row in rab.itertuples() :
            #creating a distance (diameter) for limiting the max dist of adjacent
            minx, _ , maxx, _ = row.geometry.bounds 
            rab_diameter = maxx - minx
            
            #creating the subgroup that could be affected
            rab_group = rab_adj[rab_adj.index_right==row[0]]
            for rrow in rab_group.itertuples():
                hdist = row.geometry.hausdorff_distance(rrow.geometry)

                if hdist > rab_diameter :
                    adj_long_polys.append(rrow[0]) #if hausdorff_distance is larger than the diameter add to list
           
        rab_plus = rab_adj.drop(adj_long_polys)
        
    else:
        rab['index_right'] = rab.index
        rab_plus = rab
    
    rab_plus = rab_plus[['geometry', 'index_right']] #only keeping relevant fields
    
    return rab_plus

def _rabs_center_points(gdf, center_type = 'centroid'):
    """
    From a selection of round abouts, returns an aggregated GeoDataFrame 
    per round about with extra column with center_type. 
    """
    #creating a multipolygon per RAB (as opposed to dissolving) of the entire composition of the RAB
    # temporary DataFrame where geometries is the array of pygeos geometries
    # for aggregation to work properly
    tmp = pd.DataFrame(gdf.copy())
    tmp['geometry'] = tmp.geometry.values.data

    rab_plus = gpd.GeoDataFrame(
            tmp.groupby('index_right').geometry.apply(multipolygons).rename('geometry'), crs= gdf.crs)
    # verifying that all geometries are valid
    rab_plus['geometry'] = rab_plus.apply(lambda row : make_valid(row.geometry) if not row.geometry.is_valid else row.geometry, axis = 1)
    
    if center_type == 'centroid' :
        #geometry centroid of the actual circle
        rab_plus['center_pt'] = gdf[gdf.index == gdf.index_right].geometry.centroid
    
    elif center_type == 'mean':
        # mean geometry
        ls_xy = [g.exterior.coords.xy for g in rab_plus.geometry] #extracting the points
        mean_pts = [geometry.Point(np.mean(xy[0]),np.mean(xy[1])) for xy in ls_xy]
        rab_plus['center_pt'] = gpd.GeoSeries( data = mean_pts, 
                                              index = rab_plus.index, 
                                              crs = edges.crs)
    
    # centerpoint of minimum_bounding_circle
    # minimun_bounding_circle() not available in Shapely 1.8.2 but only in 'latest'
    # --> https://shapely.readthedocs.io/en/latest/constructive.html
    # current environment has 1.8.2
    
    return rab_plus

def _coins_filtering_many_incoming(incoming_many, angle_threshold=0):
    """
    Used only for the cases when more than one incoming line touches the
    roundabout.
    """
    # figuring out which one needs to be extended and retain attributes
    coins_filter_result = []
    # For each new connection, evaluate COINS and selecet the group from which the new line belongs
    for g, x in incoming_many.groupby('line_wkt'):
        gs = gpd.GeoSeries( pd.concat([x.geometry, x.line]), crs= incoming_many.crs )
        gdf = gpd.GeoDataFrame(geometry = gs)
        gdf.drop_duplicates(inplace=True)

        coins = mm.COINS(gdf, angle_threshold=angle_threshold)
        stroke_gdf = coins.stroke_gdf()

        orig_geom_join = stroke_gdf.sjoin(gpd.GeoDataFrame(geometry = x.line), predicate= 'covers' )
        orig_geom = gpd.GeoSeries([orig_geom_join.geometry.iloc[0]], crs= incoming_many.crs)
        gs2 = gpd.GeoDataFrame(geometry = orig_geom)

        gs1 = gpd.GeoSeries(x.geometry, crs= incoming_many.crs )
        gs1 = gpd.GeoDataFrame(geometry = gs1)

        #select the the line that's covered by the joined line returned by COINS
        result_idx = gs1.sjoin(gs2 , predicate = 'covered_by').index
        coins_filter_result.extend(result_idx)
    
    incoming_many_reduced = incoming_many.loc[coins_filter_result]
    return incoming_many_reduced

def _selecting_incoming_lines (rab_plus, edges, angle_threshold=0):
    """Selecting only the lines that are touching but not covered by
    the ``rab_plus``.
    If more than one LineString is incoming to ``rab_plus``, COINS algortthm
    is used to select the line to be extended further.
    """
    # Feels a bit combersome ... Ideally there would be a DISJOINT predicate
    incoming = edges.sjoin(rab_plus , predicate = 'touches')
    incoming.rename(columns ={'index_right':'index_rab_plus'}, inplace = True )
    idx_drop =  incoming.sjoin(rab_plus, predicate = 'covered_by').index
    incoming.drop(idx_drop, axis=0, inplace =True)

    #figuring out which ends of incoming edges needs to be connected to the center_pt
    incoming['first_pt'] = incoming.geometry.apply(lambda x : geometry.Point( x.coords[0]))
    incoming['dist_fisrt_pt'] = incoming.center_pt.distance(incoming.first_pt)
    incoming['last_pt'] = incoming.geometry.apply(lambda x : geometry.Point( x.coords[-1]))
    incoming['dist_last_pt'] = incoming.center_pt.distance(incoming.last_pt)
    lines = []
    for i, row in incoming.iterrows() :
        if row.dist_fisrt_pt < row.dist_last_pt :
            lines.append(geometry.LineString([row.first_pt, row.center_pt]))
        else :
            lines.append(geometry.LineString([row.last_pt, row.center_pt]))
    incoming['line'] = gpd.GeoSeries(lines, index=incoming.index ,crs= edges.crs)

    #checking in there are more than one incoming lines arriving to the same point
    #which would create several new lines
    incoming['line_wkt'] = incoming.line.apply(lambda x : x.wkt)
    count_s = incoming.groupby(['line_wkt'])['line_wkt'].count()
    
    #separating the incoming roads that come on their own to those that come in groups
    filter_count_one  = pd.DataFrame(count_s[count_s == 1])
    filter_count_many  = pd.DataFrame(count_s[count_s > 1])
    incoming_ones = pd.merge(incoming, filter_count_one, left_on='line_wkt', right_index=True, how= 'inner')
    incoming_many = pd.merge(incoming, filter_count_many, left_on='line_wkt', right_index=True, how= 'inner')

    incoming_many_reduced = _coins_filtering_many_incoming(incoming_many, angle_threshold=angle_threshold)
    incoming_all = gpd.GeoDataFrame(pd.concat([ incoming_ones, incoming_many_reduced]), crs = edges.crs)
    
    return incoming_all

def _ext_lines_to_center(edges, incoming_all, rab_plus):
    """Extends the Linestrings geometrie to the centerpoint defined by _rabs_center_points.
    Also deleted the lines that originally defined the roundabout.
    
    Returns
    -------
    GeoDataFrame
        GeoDataFrame of with updated geometry
    """
    ## this is causing a warning too for Shapely 2.0 --> Convert the '.coords' to a numpy array
    incoming_all['geometry'] = incoming_all.apply(lambda row: linemerge([row.geometry, row.line]), axis =1)

    # deleting the original round about edges
    idx_out = edges.sjoin(rab_plus, predicate= 'covered_by', how='inner').index
    new_edges = edges.drop(idx_out, axis=0)
                        
    #replacing the modified edges in the output
    #ideally uising MAPPING but I didn't manage to make it work with multiindex!!
    
    #mantianing the same gdf shape that the original
    incoming_all = incoming_all[edges.columns]
    new_edges = pd.concat([new_edges, incoming_all])
    
    return new_edges

def roundabout_simpl(edges, polys, 
                     circom_threshold = 0.7, perc_area_threshold = 0.85, include_adjacent=True, 
                     center_type = 'centroid', angle_threshold=0):
    """Selects the roundabouts from ``polys`` to create a center point to merge all
    incoming edges.
    
    All ``edges`` attributes are preserved and geometries which belong to the selected
    roundabouts are deleted.

    If ``include_adjacent`` is passed, adjacent polygons to the actual roundabout are
    also selected for simplification if two conditions are met:
        - the area of adjacent polygons is less than the actual roundabout
        - adjacent polygons do not extend beyond the diameter of the actual roundabout.
        This uses hausdorff_distance algorithm. 

    Parameters
    ----------
    edges : GeoDataFrame
        GeoDataFrame containing LineString geometry of urban network
    polys : GeoDataFrame
        GeoDataFrame containing Polygon geometry derived from polygonyzing ``edges`` GeoDataFrame
    circom_threshold : float (default 0.7)
        Circular compactness threshold to select round abouts from ``polys`` GeoDataFrame.
        Polygons with a higher or equal threshold value will be considered for simplification.
    perc_area_threshold : float (default 0.85)
        Percentile threshold value from the area of ``polys`` to leave as input geometry.
        Polygons with a higher or equal threshold will be considered as urban blocks not considered
        for simmplification.
    include_adjacent : boolean (default True)
        Adjacent polygons to be considered also as part of the simplification.
    center_type : string (default 'centroid')
        Method to use for converging the incoming LineStrings.
        Current list of options available : 'centroid', 'mean'.
    angle_threshold : int, float (default 0)
        The angle threshold for the COINS algorithm. Only used when multiple incoming LineStrings
        arrive in the same location to the roundabout and/or to the adjacent polygons if set as True.
        Segments will only be considered a part of the same street if the deflection angle
        is above the threshold.

    Returns
    -------
    GeoDataFrame
        GeoDataFrame of with updated geometry
    """
    rab = _selecting_rabs_from_poly(polys, 
                                   circom_threshold = circom_threshold,
                                   perc_area_threshold = perc_area_threshold,
                                   include_adjacent=include_adjacent )
    rab_plus = _rabs_center_points(rab, center_type = center_type)
    incoming_all = _selecting_incoming_lines(rab_plus, edges, angle_threshold=angle_threshold)
    output = _ext_lines_to_center(edges, incoming_all, rab_plus)
    
    return output