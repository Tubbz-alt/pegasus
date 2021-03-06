% Generated by roxygen2: do not edit by hand
% Please edit documentation in R/dax.R
\name{DAX}
\alias{DAX}
\title{This job represents a sub-DAX that will be planned and executed by the workflow}
\usage{
DAX(file, id = NULL, node.label = NULL)
}
\arguments{
\item{file}{The logical name of the DAX file or the DAX File object}

\item{id}{The id of the DAX job [default: autogenerated]}

\item{node.label}{The label for this job to use in graphing}
}
\value{
The sub-DAX job
}
\description{
This job represents a sub-DAX that will be planned and executed by the workflow
}
\details{
The name argument can be either a string, or a \code{File} object. If
it is a \code{File} object, then this job will inherit its name from the
\code{File} and the \code{File} will be added in a \code{<uses>} with \code{transfer=TRUE},
\code{register=FALSE}, and \code{link=input}.
}
\examples{
daxjob1 <- DAX("foo.dax")
daxfile <- File("foo.dax")
daxjob2 <- DAX(daxfile)

}
