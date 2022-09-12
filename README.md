
# Geometry Based network simplification
## Project Summary
*[_Part of GSoC2022 / PySAL / momepy_](https://summerofcode.withgoogle.com/proposals/details/CgXX3BjY)*

The aim of this project was to explore different routes for simplifying a road network (mostly) for morphological analysis. My specific approcah was soley on geometry based operations. In summary, one PR has been merged into the main momepy code and has set the grounds of some further explorations; some of which have also made tier way into draft PRs that will be further developed past official end of GSOC2022. The accepted code was approached on the basis of developing a solution that is not only source agnostic, but also scallable. The main logic has been set by a combination of ideas by all parties; from the starting idea of polygonizing the network to the application of COINS algorithms, all the way to grouping adjacent error areas or even using the developed logic to rethinking remaining errors… it all has been a group effort by the whole team (see [agknowledgements](#agknowledgements) below). 

[Next steps](#next-steps) are crucial for the completion of the end goal. Therefore, I have left some suggestions to mark a stepping stone of what it is expected to be a cause that would benefit quite a few, as demonstrated by the interest of many (see relevant [discussion](https://github.com/pysal/momepy/discussions/361)). Also, I provided a visual guide under the [flowcharts section](#flocharts) where I portrait a small landscape of the functions that could be developed 

## Agknowledgements
I would like express my special thanks of gratitude to [Martin](https://github.com/martinfleis), [James](https://github.com/jGaboardi) and [Andres](https://github.com/amorfinv) (mentors of the project), who from the beginning accommodated the project for an extra sit in what originally was a single person opportunity. I should also mention [Gabriel](https://github.com/gsagostini) (my peer contributor) with whom it was interesting collaborating coming from different backgrounds but sharing an objective.
Their genuine patience to and constant virtual presence was vital for all my learnings. Additionally, they opened the doors to the whole [PySAL](https://pysal.org/) dev community who I had been following for a long time in the darkness -to them also an earnestl acknowledgement.

Admittedly, I would have liked to have had more tangible results and contribute further with the project. Fortunately, as proven by many other PySAL developers, contributions during previous GSOC periods have only been the beginnings of something larger.

## Repo file structure
This repository contains data, notebooks and general work in progress to the development of methods for road network simplifications.
- */[data](https://github.com/gregmaya/gsoc2022_network_simpl/tree/main/data)* : containing files used for testing.
- */[exploratory_notebooks](https://github.com/gregmaya/gsoc2022_network_simpl/tree/main/exploratory_notebooks)* : Jupyter Notebooks where the tests and debbugging where performed.
- *_others_* : including licence, .py files (with self contained functions), cache etc.

## Next Steps

## Pull Requests (PR)
- [geometry-based simplification of roundabouts #371](https://github.com/pysal/momepy/pull/371)
- [example notebook for roundabout simplification #392](https://github.com/pysal/momepy/pull/392)

## Flowcharts
Althought, quite certainly some processes and methods are likely to change, the following are some suggestions to achieve a simplified road network mainly aimed for urban morpholocical analysis.

### Detailed suggested flowchart
![alt text](https://github.com/gregmaya/gsoc2022_network_simpl/blob/main/flowchart_1.png)
*_poly_center_line() : is expected to wrap/develop the [processes teste](https://github.com/martinfleis/network_simplification) by [@martinfleis](https://github.com/martinfleis)

### Summary suggested flowchart
![alt text](https://github.com/gregmaya/gsoc2022_network_simpl/blob/main/flowchart_2.png)

## Important links
- [Tests for roadcenterline](https://github.com/martinfleis/network_simplification) by [@martinfleis](https://github.com/martinfleis)
