import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.ops import linemerge
from shapely import geometry
import momepy as mm # outp

def selecting_rabs_from_poly(gdf, circom_threshold = 0.8):
    """
    From a GeoDataFrame of polygons, returns a GDF of polygons that are 
    above the CircularCompaactness threshold as well as those adjacent ones samller in area
    that make up for a combined roundabout to be corrected
    
    Return
    ________
    GeoDataFrames : round abouts and adjacent polygons
    """
    # calculate parameters
    gdf["area"] = gdf.geometry.area
    gdf["circom"] = mm.CircularCompactness(gdf, "area").series
    
    #selecting round about polygons based on compactness
    rab = gdf[gdf.circom > circom_threshold]
    
    #selecting the adjacent areas that are of smaller than itself
    rab = gpd.sjoin(gdf, rab, predicate = 'intersects')
    rab = rab[rab.area_right >= rab.area_left]
    rab = rab[['geometry', 'index_right']]
    
    return rab

def rabs_center_points(gdf, center_type = 'centroid'):
    """
From a selection of round abouts, returns an aggregated GeoDataFrame 
per round about with extra column with center_type. 

center_type, str
    
    - centroid : (default) of the actual circleof each roundabout
    - mean:  mean point of node geometries that make up polygons
    - minimum_bounding_circle : TBD

Return
________
GeoDataFrame
"""
 #dissolving into a single geometry per round about
    rab_plus = gdf.dissolve(by = 'index_right')
    
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

def coins_filtering_many_incoming(incoming_many, angle_threshold=0):
    # From multiple incoming lines 
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

def selecting_incoming_lines (rab_plus, edges, angle_threshold=0):
    # selecting only the lines that are touching but not covered_by
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

    incoming_many_reduced = coins_filtering_many_incoming(incoming_many, angle_threshold=angle_threshold)
    incoming_all = gpd.GeoDataFrame(pd.concat([ incoming_ones, incoming_many_reduced]), crs = edges.crs)
    
    return incoming_all

def ext_lines_to_center(edges, incoming_all, rab_plus):
    # updating the original geometry 
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

def roundabout_simpl(edges, polys, circom_threshold = 0.7, center_type = 'centroid', angle_threshold=0):
    
    rab = selecting_rabs_from_poly(polys, circom_threshold = circom_threshold)
    rab_plus = rabs_center_points(rab, center_type = center_type)
    incoming_all = selecting_incoming_lines(rab_plus, edges, angle_threshold=angle_threshold)
    output = ext_lines_to_center(edges, incoming_all, rab_plus)
    
    return output